import unittest

from global_hotkey import GlobalHotkey, MAC_HOTKEYS, WINDOWS_HOTKEYS


class GlobalHotkeyTests(unittest.TestCase):
    def test_platform_defaults_have_s_and_f8(self):
        self.assertIn("Control+Option+S", MAC_HOTKEYS)
        self.assertIn("F8", MAC_HOTKEYS)
        self.assertIn("Ctrl+Alt+S", WINDOWS_HOTKEYS)
        self.assertIn("F8", WINDOWS_HOTKEYS)

    def test_windows_s_virtual_key(self):
        self.assertEqual(WINDOWS_HOTKEYS["Ctrl+Alt+S"][1], ord("S"))

    def test_dispatch_can_enqueue_without_touching_tk(self):
        events = []
        hotkey = GlobalHotkey(None, lambda: events.append("hotkey"))  # type: ignore[arg-type]
        hotkey._dispatch()
        self.assertEqual(events, ["hotkey"])


if __name__ == "__main__":
    unittest.main()
