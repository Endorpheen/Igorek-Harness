"""Reviewed Flint palette contracts against canonical rendering helpers."""

import unittest

from factory.core.tui.render import highlight as palette_fixture


class PaletteContractTests(unittest.TestCase):
    """Contract tests for the palette_fixture helpers."""

    def test_ansi_palette_color(self):
        """Basic ANSI palette colors return RGB triples; out-of-range indices return None."""
        with self.subTest("basic_color_0_black"):
            self.assertEqual(palette_fixture.ansi_palette_color(0), (0, 0, 0))

        with self.subTest("basic_color_1_red"):
            self.assertEqual(palette_fixture.ansi_palette_color(1), (170, 0, 0))

        with self.subTest("basic_color_2_green"):
            self.assertEqual(palette_fixture.ansi_palette_color(2), (0, 170, 0))

        with self.subTest("basic_color_3_yellow"):
            self.assertEqual(palette_fixture.ansi_palette_color(3), (170, 85, 0))

        with self.subTest("basic_color_4_blue"):
            self.assertEqual(palette_fixture.ansi_palette_color(4), (0, 0, 170))

        with self.subTest("basic_color_5_magenta"):
            self.assertEqual(palette_fixture.ansi_palette_color(5), (170, 0, 170))

        with self.subTest("basic_color_6_cyan"):
            self.assertEqual(palette_fixture.ansi_palette_color(6), (0, 170, 170))

        with self.subTest("basic_color_7_white"):
            self.assertEqual(palette_fixture.ansi_palette_color(7), (170, 170, 170))

        with self.subTest("out_of_range_returns_none"):
            self.assertIsNone(palette_fixture.ansi_palette_color(8))
            self.assertIsNone(palette_fixture.ansi_palette_color(255))

    def test_convert_syntect_color(self):
        """Alpha 0 selects palette by red channel, alpha 1 returns None, alpha 255 keeps RGB."""
        with self.subTest("alpha_zero_selects_palette_by_red"):
            self.assertEqual(palette_fixture.convert_syntect_color(0, 0, 0, 0), (0, 0, 0))
            self.assertEqual(palette_fixture.convert_syntect_color(1, 0, 0, 0), (170, 0, 0))

        with self.subTest("alpha_one_returns_none"):
            self.assertIsNone(palette_fixture.convert_syntect_color(12, 34, 56, 1))

        with self.subTest("alpha_opaque_keeps_rgb"):
            self.assertEqual(palette_fixture.convert_syntect_color(1, 2, 3, 255), (1, 2, 3))

    def test_ansi_256_to_rgb_basic_and_bright(self):
        """Basic and bright ANSI palette entries map to fixed RGB values."""
        with self.subTest("basic_color_0_black"):
            self.assertEqual(palette_fixture._ansi_256_to_rgb(0), (0, 0, 0))

        with self.subTest("basic_color_7_white"):
            self.assertEqual(palette_fixture._ansi_256_to_rgb(7), (170, 170, 170))

        with self.subTest("bright_color_8_gray"):
            self.assertEqual(palette_fixture._ansi_256_to_rgb(8), (85, 85, 85))

        with self.subTest("bright_color_15_white"):
            self.assertEqual(palette_fixture._ansi_256_to_rgb(15), (255, 255, 255))

    def test_ansi_256_to_rgb_cube_and_gray(self):
        """Known cube and gray colors map to expected RGB values."""
        with self.subTest("cube_color_16_black"):
            self.assertEqual(palette_fixture._ansi_256_to_rgb(16), (0, 0, 0))

        with self.subTest("cube_color_21_blue"):
            self.assertEqual(palette_fixture._ansi_256_to_rgb(21), (0, 0, 255))

        with self.subTest("cube_color_196_red"):
            self.assertEqual(palette_fixture._ansi_256_to_rgb(196), (255, 0, 0))

        with self.subTest("cube_color_231_bright_white"):
            self.assertEqual(palette_fixture._ansi_256_to_rgb(231), (255, 255, 255))

        with self.subTest("gray_color_232"):
            self.assertEqual(palette_fixture._ansi_256_to_rgb(232), (8, 8, 8))

        with self.subTest("gray_color_255"):
            self.assertEqual(palette_fixture._ansi_256_to_rgb(255), (238, 238, 238))

    def test_is_light(self):
        """Lightness check on known colors."""
        with self.subTest("black_is_not_light"):
            self.assertFalse(palette_fixture._is_light((0, 0, 0)))

        with self.subTest("white_is_light"):
            self.assertTrue(palette_fixture._is_light((255, 255, 255)))

        with self.subTest("gray_126_is_not_light"):
            self.assertFalse(palette_fixture._is_light((126, 126, 126)))

        with self.subTest("gray_128_is_light"):
            self.assertTrue(palette_fixture._is_light((128, 128, 128)))

        with self.subTest("bright_red_is_light"):
            self.assertTrue(palette_fixture._is_light((255, 85, 85)))

        with self.subTest("bright_green_is_light"):
            self.assertTrue(palette_fixture._is_light((85, 255, 85)))

    def test_style_add_modifier(self):
        """Modifier flags are independent and persist across sequential calls."""
        style = palette_fixture.Style()
        style.add_modifier(bold=True, italic=True, underline=True)
        with self.subTest("all_flags_set_independently"):
            self.assertTrue(style.bold)
            self.assertTrue(style.italic)
            self.assertTrue(style.underline)

        style = palette_fixture.Style()
        style.add_modifier(bold=True)
        style.add_modifier(italic=True)
        style.add_modifier(underline=True)
        with self.subTest("sequential_calls_accumulate"):
            self.assertTrue(style.bold)
            self.assertTrue(style.italic)
            self.assertTrue(style.underline)

        style = palette_fixture.Style()
        style.add_modifier(bold=True)
        style.add_modifier(bold=False)
        with self.subTest("explicit_false_does_not_clear"):
            self.assertTrue(style.bold)


if __name__ == "__main__":
    unittest.main()
