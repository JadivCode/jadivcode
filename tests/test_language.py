"""Language tests. Run with: python3 -m unittest discover -s tests"""

import os
import tempfile
import unittest

os.environ["JADIVCODE_GPIO"] = "sim"
os.environ["JADIVCODE_GUI"] = "headless"

from jadivcode.checker import apply_fixes, check_source  # noqa: E402
from jadivcode.errors import JadivError  # noqa: E402
from jadivcode.interpreter import Runtime  # noqa: E402


def run(source, answers=(), strict=False):
    out, err = [], []
    replies = list(answers)
    runtime = Runtime(out=out.append, err=err.append, strict=strict,
                      inp=lambda prompt: replies.pop(0) if replies else "")
    code = runtime.run_source(source)
    return out, err, code


def codes(source, severity=None):
    return [d.code for d in check_source(source) if severity in (None, d.severity)]


class BasicsTest(unittest.TestCase):
    def test_write_text_and_expression(self):
        out, _, _ = run('Write = Hello, world!\nWrite = 2 + 3 * 4\nWrite "quoted"\n'
                        "Write = total {10 / 4}")
        self.assertEqual(out, ["Hello, world!", "14", "quoted", "total 2.5"])

    def test_case_insensitive(self):
        out, _, _ = run("DEFINE Count = 2\nset COUNT = count + 1\nwRiTe = {CoUnT}")
        self.assertEqual(out, ["3"])

    def test_text_fallback_and_quotes(self):
        out, _, _ = run('Define X = 5\nWrite = X\nWrite = "X"\nWrite = Jan Novak')
        self.assertEqual(out, ["5", "X", "Jan Novak"])

    def test_comments(self):
        out, _, _ = run("// note\nWrite = a // trailing\nWrite = see https://x.y/z")
        self.assertEqual(out, ["a", "see https://x.y/z"])

    def test_braces_escape(self):
        out, _, _ = run("Write = {{literal}} {1 + 1}")
        self.assertEqual(out, ["{literal} 2"])

    def test_if_single_line_quoted_and_plain(self):
        out, _, _ = run('Define A = 3\nIf A > 2 then "Write = big" else "Write = small"\n'
                        "If A = 4 then Write = four else Write = not four")
        self.assertEqual(out, ["big", "not four"])

    def test_if_block_else_if(self):
        src = ("Define A = 2\nIf A = 1\nWrite = one\nElse If A = 2\nWrite = two\n"
               "Else\nWrite = many\nEnd")
        self.assertEqual(run(src)[0], ["two"])

    def test_text_compare_ignores_case(self):
        self.assertEqual(run('Set N = Jan\nIf N = "jan" then Write = yes')[0], ["yes"])

    def test_loops(self):
        src = ("Define S = 0\nRepeat 3 times\nSet S = S + 1\nEnd\n"
               "For I = 1 to 5\nIf I = 2 then Continue\nIf I = 4 then Break\nSet S = S + I\nEnd\n"
               "While S < 10\nSet S = S + 1\nEnd\nWrite = {S}\n"
               "For each W in List a, b\nWrite = {W}\nEnd\n"
               "Forever\nSet S = S + 1\nIf S > 12 then Break\nEnd\nWrite = {S}")
        self.assertEqual(run(src)[0], ["10", "a", "b", "13"])

    def test_functions(self):
        src = ("Function Fact N\nIf N < 2 then Return 1\nReturn N * Fact(N - 1)\nEnd\n"
               "Write = {Fact(5)}\nFunction Hi Who\nWrite = hi {Who}\nEnd\nCall Hi \"Jan\"")
        self.assertEqual(run(src)[0], ["120", "hi Jan"])

    def test_lists(self):
        src = ("Define L = List 1, 2, 3\nAdd 4 to L\nRemove 2 from L\n"
               "Write = {L} | {L #1} | {Length(L)} | {Contains(L, 4)}")
        self.assertEqual(run(src)[0], ["1, 3, 4 | 1 | 3 | ON"])

    def test_ask(self):
        out, _, _ = run("Ask Age = How old?\nWrite = {Age + 1}", answers=["41"])
        self.assertEqual(out, ["42"])

    def test_wait_units(self):
        runtime = Runtime()
        self.assertEqual(runtime.parse_duration("200ms"), 0.2)
        self.assertEqual(runtime.parse_duration("2m"), 120)
        self.assertEqual(runtime.parse_duration("1.5"), 1.5)
        runtime.set_var("Seconds", 3)
        self.assertEqual(runtime.parse_duration("Seconds"), 3)

    def test_soft_errors_and_success(self):
        out, err, _ = run("Write = {Success}\nWrite = {1 / 0}\nWrite = {Success}\n"
                          "Write = ok\nWrite = {Success} / {Error}")
        self.assertEqual(out, ["Full", "None", "ok", "Partial / Cannot divide by zero"])
        self.assertEqual(len(err), 1)

    def test_strict_stops(self):
        with self.assertRaises(JadivError):
            run("Strict ON\nWrite = {1 / 0}\nWrite = never")

    def test_unknown_statement_is_an_error(self):
        with self.assertRaises(JadivError):
            run("Wrte = hello")

    def test_braces_in_conditions_and_lone_values(self):
        src = ("Define L = {List(1, 2)}\nIf {Length(L)} = 2 then Write = two\n"
               "For each I in {L}\nWrite = {I}\nEnd")
        self.assertEqual(run(src)[0], ["two", "1", "2"])

    def test_stop_ends_program(self):
        self.assertEqual(run("Write = a\nStop\nWrite = b")[0], ["a"])


class LibraryTest(unittest.TestCase):
    def test_user_example_one(self):
        src = ('import GPIO\nImport Math\nDEFINE GPIO1 = GPIO #1\nSet GPIO1 = ON\nWait 10ms\n'
               'Set GPIO1 = OFF\nWrite = math result follows {Math "5x5"}\n'
               'If Success = "Full" then "Write = OK" else "Write = ERROR"')
        out, _, _ = run(src)
        self.assertEqual(out[-2:], ["math result follows 25", "OK"])
        self.assertIn("[GPIO simulator] pin 1 -> ON", out)
        self.assertEqual(codes(src), [])

    def test_user_example_two(self):
        src = ('Import GUI\nImport Math\nImport GPIO\nDEFINE WIndow = GUI WIndow #1\n'
               'Start WIndow\nDEFINE WindowWrite = GUI Write {} on WIndow #1\n'
               'DEFINE WindowSet = GUI Set {} on WIndow #1\nWindowSet Title = Test app\n'
               'WindowWrite Button "X = 10, Y 10" "B01" = Press me\n'
               'DEFINE ButtonPress = GUI Read Button {} on WIndow #1\n'
               'On ButtonPress "WindowWrite Text "X = 50, Y= 5" "T01" = Pressed" Else loop\n'
               'GUI Press "B01" on Window #1\nWait 10ms\n'
               'Write = {GUI Read Text "T01" on Window #1}')
        out, _, _ = run(src)
        self.assertEqual(out[-1], "Pressed")
        self.assertEqual(codes(src, "error"), [])

    def test_math(self):
        src = ('Import Math\nWrite = {Math "2 x 3 + sqrt(16)"} {Math Round 3.14159 2} '
               '{Math Max 4 9 2} {Math Power 2 8} {Math "10 ÷ 4"} {Math Sin 90}\n'
               'Define R = {Math Random 1 6}\nIf R >= 1 and R <= 6 then Write = dice ok')
        self.assertEqual(run(src)[0], ["10 3.14 9 256 2.5 1", "dice ok"])

    def test_library_name_in_plain_text(self):
        self.assertEqual(run("Import Math\nWrite = Math is fun")[0], ["Math is fun"])

    def test_missing_import(self):
        _, err, _ = run('Write = a\nGPIO Write #4 = ON')
        self.assertIn("not imported", err[0])

    def test_gpio_input_event_and_range(self):
        src = ('Import GPIO\nDefine Button = GPIO Input #4\n'
               'On Button Pressed "Write = click {Event}"\nGPIO Simulate #4 = ON\nWait 10ms\n'
               'Write = {Button}\nDefine Bad = GPIO #99')
        out, err, _ = run(src)
        self.assertIn("click ON", out)
        self.assertEqual(out[-1], "ON")
        self.assertIn("GPIO #99 does not exist", err[0])

    def test_gpio_read_before_set_keeps_output(self):
        out, err, _ = run("Import GPIO\nDefine Lamp = GPIO #17\n"
                          "If Lamp = ON then Set Lamp = OFF else Set Lamp = ON\nWrite = {Lamp}")
        self.assertEqual((out[-1], err), ("ON", []))

    def test_gpio_pwm(self):
        out, err, _ = run("Import GPIO\nDefine Fan = GPIO PWM #18\nSet Fan = 40\n"
                          "Write = {Fan}\nSet Fan = 140")
        self.assertIn("40", out)
        self.assertEqual(len(err), 1)

    def test_file_and_system(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "n.txt")
            src = (f'Import File\nImport System\nFile Write "{path}" = one\n'
                   f'File Append "{path}" = two\nWrite = {{File Lines "{path}"}}\n'
                   f'Write = {{System Run "echo" "a b; ls"}}\nFile Delete "{path}"\n'
                   f'Write = {{File Exists "{path}"}}')
            self.assertEqual(run(src)[0], ["one, two", "a b; ls", "OFF"])

    def test_widget_values(self):
        src = ('Import GUI\nDefine W = GUI Window #1\nGUI Write Input "I1" on W = hello\n'
               'GUI Write Checkbox "C1" on W = Agree\nGUI Set "C1" Value on W = ON\n'
               'Write = {GUI Read Input "I1" on W} {GUI Read Checkbox "C1" on W}\n'
               'GUI Write Button "I1" on W = clash')
        out, err, _ = run(src)
        self.assertEqual(out, ["hello ON"])
        self.assertIn("already exists", err[0])


class CheckerTest(unittest.TestCase):
    def test_clean_program(self):
        self.assertEqual(codes("Define A = 1\nWrite = {A}"), [])

    def test_typos_and_fixes(self):
        src = "Import Mth\nWrte = hi\nIf 1 = 1\nWrite = x"
        found = codes(src)
        for code in ("E331", "E240", "E202"):
            self.assertIn(code, found)
        fixed, count = apply_fixes(src, check_source(src))
        self.assertEqual(count, 3)
        self.assertEqual(codes(fixed, "error"), [])

    def test_undefined_name_suggestion(self):
        diags = [d for d in check_source("Define Counter = 1\nWrite = {Countr}")
                 if d.severity == "error"]
        self.assertEqual(diags[0].code, "E301")
        self.assertIn("Counter", diags[0].hint)

    def test_missing_import_fix(self):
        src = 'Write = {Math "1+1"}'
        fixed, _ = apply_fixes(src, check_source(src))
        self.assertTrue(fixed.startswith("Import Math\n"))
        self.assertEqual(run(fixed)[0], ["2"])

    def test_security_rules(self):
        src = ('Import Web\nImport System\nImport File\nImport WebUI\n'
               'Define ApiToken = abc123\nWrite = {ApiToken}\n'
               'Web Get "http://example.com/x"\nAsk Folder = Which?\n'
               'Write = {System Run "ls {Folder}"}\nFile Delete "{Folder}"\n'
               'Define Page = WebUI Page #1\nWebUI Set Host on Page #1 = 0.0.0.0\n'
               'Forever\nWrite = spin\nEnd')
        found = codes(src)
        self.assertIn("S001", codes('Import Web\nWeb Header "Authorization" = Bearer abc'))
        for code in ("S001", "S002", "S003", "S004", "S005", "S006", "W110"):
            self.assertIn(code, found)

    def test_safe_variants_are_quiet(self):
        src = ('Import Web\nImport System\nImport WebUI\n'
               'Define ApiToken = {System Env "API_TOKEN"}\n'
               'Web Header "Authorization" = Bearer {ApiToken}\n'
               'Web Get "https://example.com/x"\nWeb Get "http://192.168.1.5/x"\n'
               'Define Page = WebUI Page #1\nWebUI Set Host on Page #1 = 0.0.0.0\n'
               'WebUI Set Password on Page #1 = {System Env "PAGE_PASSWORD"}\n'
               'Forever\nWait 1s\nEnd')
        self.assertEqual([c for c in codes(src) if c.startswith(("S", "E"))], [])

    def test_static_pin_range(self):
        self.assertEqual(codes("Import GPIO\nGPIO Write #40 = ON").count("E437"), 1)


if __name__ == "__main__":
    unittest.main()
