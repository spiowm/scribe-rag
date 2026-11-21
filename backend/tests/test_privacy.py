from src.ingestion.privacy import is_secret_file, mask


def test_cards_masked_only_when_luhn_valid():
    text, hits = mask("карта 4111 1111 1111 1111, замовлення 1234567890123")
    assert "[номер картки]" in text and "1234567890123" in text
    assert hits == 1


def test_hex_ids_in_links_untouched():
    # цифровий відрізок проходить Luhn, але це частина id сторінки
    link = "https://www.notion.so/deadbeef4111111111111111cafebabe"
    assert mask(f"про кольори — [тут]({link})") == (f"про кольори — [тут]({link})", 0)
    text, hits = mask("картка:4111111111111111грн")
    assert text == "картка:[номер картки]грн" and hits == 1


def test_iban_masked_with_checksum():
    good = "UA62 0000 0000 0000 0000 0000 0000 1"
    text, hits = mask(f"рахунок {good}; код UA00000000000000000001")
    assert "[IBAN]" in text and "UA00000000000000000001" in text
    assert hits == 1


def test_masked_when_followed_by_number_or_word():
    cases = {
        "Картка 4111 1111 1111 1111 50 грн": "Картка [номер картки] 50 грн",
        "4111 1111 1111 1111 12/27": "[номер картки] 12/27",
        "IBAN UA620000000000000000000000001 EUR": "IBAN [IBAN] EUR",
        "UA62 0000 0000 0000 0000 0000 0000 1 TOV": "[IBAN] TOV",
        "UA620000000000000000000000001 2024": "[IBAN] 2024",
    }
    for src, expected in cases.items():
        assert mask(src) == (expected, 1), src


def test_phones_and_dates_untouched():
    src = "Тел. +380000000001, 0000000002, 12.05.2026, 2026-05-12"
    assert mask(src) == (src, 0)


def test_secret_files():
    assert is_secret_file("Passwords.docx")
    assert is_secret_file("Паролі від акаунтів")
    assert not is_secret_file("Паспорт проєкту.pdf")


def test_infobooks_are_secret_files():
    """Назви тестових інфобуків і звичайних документів."""
    for name in (
        "Інфобук Приклад 2023",
        "інфобук",
        "Інфобук форма відповіді",
        "Infobook Demo'2025",
        "Infobook Demo`14 (Responses)",
        "[Demo'15] Team Infobook",
        "[Demo'2023] Information Book (Відповіді)",
        "Information book for Workshop",
        "Information book",
    ):
        assert is_secret_file(name), name
    # схожі назви — звичайні документи
    for name in (
        "Хендбук дизайнера",
        "[Demo] FR Booklet eng.pdf",
        "InformationalGuide.pdf",
        "Information for secretary.xlsx",
        "Інфостійки",
        "Інформація про пакети",
        "Брендбук",
    ):
        assert not is_secret_file(name), name


def test_numbers_that_pass_luhn_by_chance_untouched():
    # обидва проходять Luhn: двійковий рядок і числовий сегмент URL
    for src in (
        "01110110 01000111 01100101 00100000",
        "https://example.test/item/1000000000000008.2",
        # ряд таблиці цін: 4 000 6 000 8 000 10 001 разом проходять Luhn
        "Вартість пакету, грн 4 000 6 000 8 000 10 001",
    ):
        assert mask(src) == (src, 0), src
    assert mask("ПРОСТІР 9804000000000009") == ("ПРОСТІР [номер картки]", 1)
    assert mask("Amex 3700 000000 00002") == ("Amex [номер картки]", 1)
