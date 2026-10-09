import asyncio
import json
import os
import time


class AgyError(Exception):
    """Будь-який збій agy: не стартував, упав, тайм-аут, помилка у відповіді."""


class AgyProcess:
    def __init__(
        self,
        bin_path: str,
        home: str,
        workdir: str,
        model: str,
        schema_path: str,
        turn_timeout: float,
    ):
        self.bin_path = bin_path
        self.home = home
        self.workdir = workdir
        self.model = model
        self.schema_path = schema_path
        self.turn_timeout = turn_timeout
        self.proc: asyncio.subprocess.Process | None = None
        self.conversation_id: str | None = None
        self.last_usage: dict = {}
        self.resumed: bool = False  # чи відкрилась саме та розмова, що просили
        self.started_at: float = 0.0

    def _env(self) -> dict:
        env = {
            "HOME": self.home,
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "TZ": "Europe/Kyiv",
            "AGY_CLI_DISABLE_AUTO_UPDATE": "1",
        }
        for key in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
            if os.environ.get(key):
                env[key] = os.environ[key]
        return env

    async def _read_event(self, timeout: float) -> dict:
        """Читає з виходу agy наступу подію -- один рядок JSON"""
        proc = self.proc
        if proc is None or proc.stdout is None or proc.stderr is None:
            raise AgyError("agy is not running")
        while True:
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout)
            except TimeoutError as exc:
                raise AgyError("Timeout waiting for event") from exc
            if not raw:
                # Порожньо — значить agy закрив вихід, тобто процес завершився.
                # Причину він пише в окремий канал помилок, stderr.
                stderr = (await proc.stderr.read()).decode(errors="replace")
                raise AgyError(f"agy finished unexpectedly. stderr: {stderr[-500:]}")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                continue  # рядок не JSON (якесь попередження) — пропускаємо

    async def start(
        self, conversation_id: str | None = None, last_usage: dict | None = None
    ) -> str:
        """Запускає agy і вертає номер розмови з першої події -- init"""
        args = [
            self.bin_path,
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--model",
            self.model,
            "--json-schema",
            self.schema_path,
        ]
        if conversation_id:
            args += ["--conversation", conversation_id]

        try:
            self.proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=self.workdir,
                env=self._env(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=16 * 1024 * 1024,
            )
        except OSError as exc:
            # Нема бінарника або теки під cwd. Без обгортки це не AgyError,
            # тому не повторюється і не видно серед інших збоїв agy.
            raise AgyError(f"не вдалося запустити agy: {exc}") from exc
        self.last_usage = dict(last_usage or {})
        self.started_at = time.monotonic()

        event = await self._read_event(timeout=30)
        if event.get("event") != "init":
            raise AgyError(f"очікував init, отримав {event.get('event')}")

        new_id = event.get("conversation_id")
        if not new_id:
            raise AgyError("init прийшов без conversation_id")
        self.conversation_id = new_id
        # Невідомий номер agy мовчки замінює новою розмовою — тому звіряємо.
        self.resumed = bool(conversation_id) and new_id == conversation_id
        return new_id

    async def send(self, text: str) -> dict:
        """Надсилає одне повідомлення й вертає повідомлення з відповіддю."""
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise AgyError("agy is not running")
        line = json.dumps(
            {"event": "user", "message": {"content": text}}, ensure_ascii=False
        )
        proc.stdin.write((line + "\n").encode())
        await proc.stdin.drain()

        while True:
            event = await self._read_event(timeout=self.turn_timeout)
            if event.get("event") != "result":
                continue
            result = event["result"]
            if result.get("status") != "SUCCESS":
                raise AgyError(result.get("error") or f"status {result.get('status')}")

            usage = result.get("usage") or {}
            keys = ("input_tokens", "cache_read_tokens", "output_tokens")
            result["delta"] = {
                k: usage.get(k, 0) - self.last_usage.get(k, 0) for k in keys
            }
            self.last_usage = {k: usage.get(k, 0) for k in keys}
            return result

    async def close(self) -> None:
        proc = self.proc
        if proc is None:
            return
        if proc.returncode is None:  # None означає процес ще живий
            try:
                # Закрити вхід = «повідомлень більше не буде».
                # agy це бачить і завершується сам.
                if proc.stdin is not None:
                    proc.stdin.close()
                await asyncio.wait_for(proc.wait(), 10)
            except (TimeoutError, ConnectionError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
        self.proc = None
