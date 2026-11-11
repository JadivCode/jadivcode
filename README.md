# JadivCode

A super simple programming language for Linux. Programs read almost like
sentences, run with one command, and can switch Raspberry Pi pins, open
windows, serve web pages and talk to webhooks.

```jadivcode
Import GPIO
Import Math

Define Lamp = GPIO #17
Set Lamp = ON
Wait 5s
Set Lamp = OFF

Write = The answer is {Math "5x5"}
If Success = "Full" then "Write = OK" else "Write = ERROR"
```

```bash
jadivcode run lamp.jdv
```

Full documentation: <https://jadivcode.github.io>

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/JadivCode/jadivcode/main/install.sh | bash
```

The installer needs Python 3.10 or newer, puts everything in your home
folder and never uses `sudo`. Remove JadivCode again with
`bash install.sh --uninstall`.

Optional extras:

| For | Install |
|---|---|
| GUI windows | `sudo apt install python3-tk` |
| Real Raspberry Pi pins | `sudo apt install python3-gpiozero python3-lgpio` |
| The AI helper | `python3 -m pip install --user anthropic` |

## Commands

| Command | What it does |
|---|---|
| `jadivcode run file.jdv` | Check the program, then run it |
| `jadivcode run --sim file.jdv` | Run with simulated pins and windows |
| `jadivcode check file.jdv` | Find mistakes and unsafe code without running |
| `jadivcode check --fix file.jdv` | Apply the fixes the checker can make itself |
| `jadivcode new name` | Create `name.jdv` from a template |
| `jadivcode libs [Name]` | List libraries and their commands |
| `jadivcode repl` | Try statements interactively |
| `jadivcode explain file.jdv` | Ask the optional AI helper (sends the file to the Claude API) |

## The language in one minute

```jadivcode
// Variables and text
Define Count = 3
Set Count = Count + 1
Write = Count is {Count}

// Questions
Ask Name = What is your name?

// Decisions
If Count > 3
    Write = big
Else
    Write = small
End

// Loops
Repeat 3 times
    Write = hello
End

// Lists and functions
Define Colors = List red, green, blue
For each Color in Colors
    Write = {Color}
End

Function Double N
    Return N * 2
End
Write = {Double(21)}
```

Names and keywords are not case sensitive: `Write`, `write` and `WRITE`
are the same.

## Libraries

| Library | Purpose |
|---|---|
| `GPIO` | Raspberry Pi pins (BCM numbers), simulated on a PC |
| `Math` | Calculations, rounding, random numbers |
| `GUI` | Desktop windows: text, buttons, inputs, checkboxes, sliders |
| `WebUI` | The same items as a web page |
| `Web` | HTTP requests and incoming webhooks |
| `File` | Read and write text files |
| `System` | Run programs, read the environment |
| `Time` | Date and time |

Run `jadivcode libs GUI` to see every command of a library.

## The checker

`jadivcode run` checks a program before starting it. Besides syntax errors
and misspelled names (with "Did you mean ...?" suggestions and automatic
fixes) it warns about unsafe code:

| Code | Finding |
|---|---|
| `S001` | A password, token or key written directly in the program |
| `S002` | Data sent over unencrypted `http://` |
| `S003` | A web page or webhook server open to the network without a password |
| `S004` | A value pasted into a command line |
| `S005` | A file name that comes from outside the program |
| `S006` | A secret printed on screen |
| `W110` | A loop that never pauses |

## Development

```bash
python3 -m unittest discover -s tests
python3 -m jadivcode run examples/hello.jdv
```

The interpreter is pure Python with no required dependencies. Each library
is one file in `jadivcode/libs/`.

## License

MIT License. See [LICENSE](LICENSE).
