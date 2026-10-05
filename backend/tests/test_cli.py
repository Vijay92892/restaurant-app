import io

from app.cli import read_password_line


def test_password_from_stdin_drops_newline_and_powershell_bom() -> None:
    assert (
        read_password_line(io.StringIO("\N{BYTE ORDER MARK}Spice-Garden-2026\r\n"))
        == "Spice-Garden-2026"
    )
    assert read_password_line(io.StringIO("plain-pass-123\n")) == "plain-pass-123"
    # Interior and trailing spaces are part of the password.
    assert read_password_line(io.StringIO(" a b \n")) == " a b "
