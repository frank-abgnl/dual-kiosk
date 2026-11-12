from pathlib import Path
from unittest.mock import MagicMock, patch

from core import kiosk_shield
from core.kiosk_shield import patch_super_keys, restore_all_keymaps, KioskShield
from core.paths import KEYMAP_DIR


def test_patch_super_keys():
    """Verify Super keys are patched only in xkb_symbols, untouched in xkb_compat, and idempotent."""
    sample_xkb = """
xkb_keymap {
    xkb_compat "complete" {
        interpret Super_L {
            action = SetMods(modifiers=Mod4);
        };
        interpret Super_R {
            action = SetMods(modifiers=Mod4);
        };
    };

    xkb_symbols "pc" {
        key <LWIN> { [ Super_L ] };
        key <RWIN> { [ Super_R ] };
        key <SPCE> { [ space ] };
    };
};
"""
    # First pass
    patched, count = patch_super_keys(sample_xkb)
    assert count == 2, f"Expected 2 replacements, got {count}"
    assert "Super_L" in patched, "Super_L in xkb_compat should remain untouched"
    assert "Super_R" in patched, "Super_R in xkb_compat should remain untouched"
    assert "key <LWIN> { [ NoSymbol ] };" in patched
    assert "key <RWIN> { [ NoSymbol ] };" in patched

    # Second pass: idempotent
    patched_second, count_second = patch_super_keys(patched)
    assert count_second == 0, f"Second pass should make 0 replacements, got {count_second}"
    assert patched_second == patched, "Second pass text must be identical"


def test_restore_all_keymaps_called(tmp_path):
    """Verify restore_all_keymaps restores backup files in KEYMAP_DIR."""
    backup_file = tmp_path / "orig_3.xkb"
    backup_file.write_text("xkb_keymap dummy")

    commands = []

    def mock_run(args, **kwargs):
        commands.append(args)
        return "", "", 0

    with patch.object(kiosk_shield, "KEYMAP_DIR", tmp_path), \
         patch.object(kiosk_shield, "_run", side_effect=mock_run):
        
        ok = restore_all_keymaps()
        assert ok is True
        assert any("orig_3.xkb" in str(arg) for cmd in commands for arg in cmd)


def test_shield_stop_restores_keymaps(tmp_path):
    """Verify stopping KioskShield calls restore_all_keymaps."""
    mock_kiosk = MagicMock()
    shield = KioskShield(mock_kiosk)

    with patch("core.kiosk_shield.restore_all_keymaps") as mock_restore:
        shield._restore_keymaps()
        mock_restore.assert_called_once()
