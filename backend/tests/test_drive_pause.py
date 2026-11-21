import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.config import Settings, settings
from src.ingestion import pipeline
from src.ingestion.schemas import GENERAL
from src.models.access import Drive
from src.models.document import Document

WORK = "work-drive"
PAUSED = "paused-drive"
COMMON = "common-drive"
MIME = "application/pdf"


@pytest.fixture(autouse=True)
def pause(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_GENERAL_ID", COMMON)
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_IDS", f"{WORK},{PAUSED}")
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_PAUSED_IDS", f" , {PAUSED}, ")


def test_pause_accepts_work_ids_only():
    kwargs = {
        "GEMINI_API_KEY": "unused",
        "MONGO_URI": "mongodb://127.0.0.1:1",
        "GOOGLE_DRIVE_GENERAL_ID": COMMON,
        "GOOGLE_DRIVE_GENERAL_FOREIGN": "foreign-drive",
    }
    for shared in (COMMON, "foreign-drive"):
        with pytest.raises(ValidationError, match="accepts only work drives"):
            Settings(**kwargs, GOOGLE_DRIVE_PAUSED_IDS=shared)


def test_missing_general_id_stops_before_listing(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_GENERAL_ID", "")
    with pytest.raises(RuntimeError, match="GOOGLE_DRIVE_GENERAL_ID"):
        asyncio.run(pipeline.gdrive_targets(object()))


def test_listing_skips_paused_shortcuts_from_general_and_work():
    class DriveListing:
        async def list_drives(self):
            return [{"id": drive, "name": drive} for drive in (COMMON, WORK)]

        async def list_drive(self, drive_id, mimes, *, paused_drives):
            assert drive_id in (COMMON, WORK)
            assert paused_drives == {PAUSED}
            return [
                {
                    "id": name,
                    "name": name,
                    "driveId": physical,
                    "mimeType": MIME,
                    "path": "",
                    "size": "1",
                }
                for name, physical in (
                    (drive_id, drive_id),
                    (f"link-{drive_id}", PAUSED),
                )
            ]

    assert settings.GOOGLE_DRIVES == [WORK, PAUSED]
    targets = asyncio.run(pipeline.gdrive_targets(DriveListing()))
    assert {(item["id"], item["scope"]) for item in targets} == {
        (COMMON, GENERAL),
        (WORK, WORK),
    }


def test_file_and_folder_shortcuts_do_not_read_paused_drive():
    from src.connectors.gdrive import GDriveConnector

    drive = object.__new__(GDriveConnector)
    folder = GDriveConnector.FOLDER

    def shortcut(fid, kind):
        return {
            "name": fid,
            "mimeType": GDriveConnector.SHORTCUT,
            "parents": [COMMON],
            "shortcutDetails": {"targetId": fid, "targetMimeType": kind},
        }

    async def fake_list(params):
        assert params["driveId"] == COMMON
        if GDriveConnector.SHORTCUT in params["q"]:
            return [
                shortcut("allowed-folder", folder),
                shortcut("paused-folder", folder),
                shortcut("paused-file", MIME),
                shortcut("allowed-file", MIME),
            ]
        return []

    async def fake_get(url, params):
        fid = url.rsplit("/", 1)[-1]
        scope = PAUSED if fid.startswith("paused-") else WORK
        return SimpleNamespace(
            json=lambda: {"id": fid, "mimeType": MIME, "driveId": scope}
        )

    async def fake_children(folder_id):
        assert folder_id == "allowed-folder"
        return [
            {"id": "contained", "name": "Contained", "mimeType": MIME, "driveId": WORK},
            shortcut("paused-nested-folder", folder),
            shortcut("paused-nested-file", MIME),
        ]

    drive._list = fake_list
    drive._get_with_retry = fake_get
    drive.list_children = fake_children
    listed = asyncio.run(drive.list_drive(COMMON, {MIME}, paused_drives={PAUSED}))
    assert {entry["id"] for entry in listed} == {"allowed-file", "contained"}


def test_reextract_does_not_delete_paused_rows(monkeypatch):
    async def targets(_drive):
        return []

    async def meta(_db, source):
        assert source == "gdrive"
        return {
            "paused": pipeline.documents.Meta("t", PAUSED, "", 50),
            "missing": pipeline.documents.Meta("t", WORK, "", 50),
        }

    async def upsert(_db, rows):
        assert rows == []

    async def delete(_db, ids):
        assert ids == {"missing"}
        return len(ids)

    monkeypatch.setattr(pipeline, "gdrive_targets", targets)
    monkeypatch.setattr(pipeline.documents, "meta", meta)
    monkeypatch.setattr(pipeline.documents, "upsert_documents", upsert)
    monkeypatch.setattr(pipeline.documents, "delete_documents", delete)
    result = asyncio.run(pipeline.extract_gdrive(None, None, None, everything=True))
    assert result.removed == 1
    assert result.rows == []


@pytest.mark.parametrize("full", [False, True])
def test_index_retains_paused_points_and_resumes(monkeypatch, full):
    async def meta(_db, _source):
        return {
            "paused-changed": pipeline.documents.Meta("new", PAUSED, "", 50),
            "paused-unindexed": pipeline.documents.Meta("new", PAUSED, "", 50),
            "moved-to-paused": pipeline.documents.Meta("new", PAUSED, "", 50),
            "active": pipeline.documents.Meta("new", WORK, "", 50),
        }

    async def load(_db, ids):
        return [
            Document(
                source_id=sid,
                source="gdrive",
                title=sid,
                url="",
                text="У документі достатньо вмісту для створення фрагмента.",
                scope=WORK if sid == "active" else PAUSED,
                last_edited="new",
                path="",
            )
            for sid in ids
        ]

    class Qdrant:
        async def index_state(self, source):
            assert source == "gdrive"
            return {
                "paused-changed": ("old", PAUSED, ""),
                "paused-index-only": ("old", PAUSED, ""),
                "moved-to-paused": ("old", WORK, ""),
                "active": ("old", WORK, ""),
                "removed": ("old", WORK, ""),
            }

        async def reindex(self, fresh_nodes, stale_ids):
            self.stale = stale_ids
            self.fresh = {node.metadata["source_id"] for node in fresh_nodes}
            return len(fresh_nodes)

    monkeypatch.setattr(pipeline.documents, "meta", meta)
    monkeypatch.setattr(pipeline.documents, "load", load)
    qdrant = Qdrant()
    asyncio.run(pipeline.index(qdrant, None, "gdrive", full=full))
    assert qdrant.fresh == {"active"}
    assert qdrant.stale == {"active", "removed", "moved-to-paused"}

    monkeypatch.setattr(settings, "GOOGLE_DRIVE_PAUSED_IDS", "")
    asyncio.run(pipeline.index(qdrant, None, "gdrive"))
    assert qdrant.fresh == {
        "active",
        "paused-changed",
        "paused-unindexed",
        "moved-to-paused",
    }
    assert "paused-index-only" in qdrant.stale


def test_drive_access_view_and_direct_read_exclude_paused_for_any_role(monkeypatch):
    from src.api.routers import drives
    from src.models.access import DriveOptOut
    from src.repository import access, documents

    group = "group@example.test"
    rows = [
        SimpleNamespace(id=WORK, name="Робочий", principals=[group]),
        SimpleNamespace(id=PAUSED, name="Призупинений", principals=[group]),
    ]
    user = SimpleNamespace(id=1, telegram_id=3, phone_number="+10000000000")

    class Db:
        async def scalars(self, statement):
            [column] = statement.column_descriptions
            if statement.selected_columns.keys() == ["id"]:
                return [row.id for row in rows]
            if column["entity"] is DriveOptOut:
                return []
            assert column["entity"] is Drive
            return rows

        async def execute(self, statement):
            (selected,) = statement.compile().params.values()
            found = [
                tuple(
                    getattr(row, col["name"]) for col in statement.column_descriptions
                )
                for row in rows
                if row.id in selected
            ]
            return SimpleNamespace(tuples=lambda: found)

        async def get(self, cls, sid):
            assert cls is Document
            return SimpleNamespace(scope=PAUSED if sid == "closed" else WORK)

    async def member_principals(_db, _member):
        return {group}

    async def find_member_by_phone(_phone):
        return {"email": "synthetic@example.test"}

    monkeypatch.setattr(access, "member_principals", member_principals)
    mongo = SimpleNamespace(find_member_by_phone=find_member_by_phone)
    db = Db()

    async def verify():
        _, scopes = await access.caller(db, mongo, user)
        assert scopes == [GENERAL, WORK]
        view = await drives.list_drives(user, db, mongo)
        assert [row["id"] for row in view["drives"]] == [WORK]
        assert view["no_access"] == []
        with pytest.raises(HTTPException) as exc:
            await drives.toggle(SimpleNamespace(drive_id=PAUSED), user, db, mongo)
        assert exc.value.status_code == 403
        assert await documents.get_document(db, "closed", scopes) is None
        assert (await documents.get_document(db, "open", scopes)).scope == WORK

    asyncio.run(verify())
    monkeypatch.setattr(settings, "ADMIN_TELEGRAM_ID", user.telegram_id)
    asyncio.run(verify())


def test_admin_refresh_preserves_paused_acl_without_showing_it():
    from src.api.routers import admin

    shared, closed = "shared@example.test", "closed@example.test"

    def row(drive, group):
        return SimpleNamespace(id=drive, name=drive, principals=[group], refreshed_at=1)

    class GDrive:
        async def list_drives(self):
            return [{"id": drive, "name": drive} for drive in (WORK, PAUSED)]

        async def drive_principals(self, drive):
            assert drive == WORK
            return [shared]

    class Db:
        async def execute(self, stmt):
            if stmt.is_delete:
                [kept] = stmt.compile().params.values()
                assert set(kept) == {WORK, PAUSED}
            elif stmt.is_select:
                return [(shared, 2, 1), (closed, 1, 1)]

        async def scalars(self, stmt):
            return [row(WORK, shared), row(PAUSED, closed)]

        async def commit(self):
            pass

    overview = asyncio.run(admin.refresh_drives(None, Db(), GDrive()))
    assert [drive["id"] for drive in overview["drives"]] == [WORK]
    assert set(overview["groups"]) == {shared}
