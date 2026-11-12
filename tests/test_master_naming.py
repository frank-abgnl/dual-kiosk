from pathlib import Path
from unittest.mock import MagicMock, patch

from core.xinput_seating import XInputSeatingEngine


def test_master_name_scheme():
    """Verify master_name generates canonical Seat_<UPPER> name."""
    assert XInputSeatingEngine.master_name("seat2") == "Seat_SEAT2"
    assert XInputSeatingEngine.master_name("seat1") == "Seat_SEAT1"
    assert XInputSeatingEngine.master_name("kiosk_b") == "Seat_KIOSK_B"


def test_no_literal_seat2_in_gui_or_core():
    """Verify string 'Seat2' does not appear in gui/ or core/ code."""
    root = Path(__file__).resolve().parent.parent
    for folder in ("core", "gui"):
        for py_path in (root / folder).rglob("*.py"):
            content = py_path.read_text(encoding="utf-8")
            assert '"Seat2"' not in content, f'Found literal "Seat2" in {py_path}'


def test_manual_attach_creates_canonical_master():
    """After a manual attach, exactly one non-core master is created per secondary seat."""
    engine = XInputSeatingEngine()
    commands = []

    def mock_run(args, **kwargs):
        commands.append(args)
        if args[:2] == ["xinput", "create-master"]:
            return "", "", 0
        if args == ["xinput", "list"]:
            # Simulate hierarchy with created master
            simulated = (
                "⎡ Virtual core pointer                    \tid=2\t[master pointer  (3)]\n"
                "⎣ Virtual core keyboard                   \tid=3\t[master keyboard (2)]\n"
                "⎡ Seat_SEAT2 pointer                      \tid=10\t[master pointer  (11)]\n"
                "⎣ Seat_SEAT2 keyboard                     \tid=11\t[master keyboard (10)]\n"
            )
            return simulated, "", 0
        return "", "", 0

    with patch("core.xinput_seating.run_command", side_effect=mock_run):
        m_name = engine.master_name("seat2")
        assert m_name == "Seat_SEAT2"
        ptr_id, kbd_id = engine.create_master_pair(m_name)
        assert ptr_id == 10
        assert kbd_id == 11

        masters = engine.get_existing_masters()
        # Verify exactly one non-core master exists
        non_core = [k for k in masters if k != "Virtual core"]
        assert non_core == ["Seat_SEAT2"]
