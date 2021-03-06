#!/usr/bin/env python3

import argparse
import os
import sys
import subprocess
import termios
from typing import cast, Any, Dict, Iterable, List, Optional, Union

__author__ = "Ingo Heimbach"
__email__ = "i.heimbach@fz-juelich.de"
__copyright__ = "Copyright © 2019 Forschungszentrum Jülich GmbH. All rights reserved."
__license__ = "MIT"
__version_info__ = (0, 4, 5)
__version__ = ".".join(map(str, __version_info__))


DEFAULT_MENU_CURSOR = "> "
DEFAULT_MENU_CURSOR_STYLE = ("fg_red", "bold")
DEFAULT_MENU_HIGHLIGHT_STYLE = ("standout",)
DEFAULT_CYCLE_CURSOR = True


class InvalidStyleError(Exception):
    pass


class NoMenuEntriesError(Exception):
    pass


class TerminalMenu:
    _codename_to_capname = {
        "bg_black": "setab 0",
        "bg_blue": "setab 4",
        "bg_cyan": "setab 6",
        "bg_gray": "setab 7",
        "bg_green": "setab 2",
        "bg_purple": "setab 5",
        "bg_red": "setab 1",
        "bg_yellow": "setab 3",
        "bold": "bold",
        "colors": "colors",
        "cursor_down": "cud1",
        "cursor_invisible": "civis",
        "cursor_up": "cuu1",
        "cursor_visible": "cnorm",
        "delete_line": "dl1",
        "down": "kcud1",
        "enter_application_mode": "smkx",
        "exit_application_mode": "rmkx",
        "fg_black": "setaf 0",
        "fg_blue": "setaf 4",
        "fg_cyan": "setaf 6",
        "fg_gray": "setaf 7",
        "fg_green": "setaf 2",
        "fg_purple": "setaf 5",
        "fg_red": "setaf 1",
        "fg_yellow": "setaf 3",
        "italics": "sitm",
        "reset_attributes": "sgr0",
        "standout": "smso",
        "underline": "smul",
        "up": "kcuu1",
    }
    _name_to_control_character = {"enter": "\012", "escape": "\033"}
    _codenames = tuple(_codename_to_capname.keys())
    _codename_to_terminal_code = None  # type: Optional[Dict[str, str]]
    _terminal_code_to_codename = None  # type: Optional[Dict[str, str]]

    def __init__(
        self,
        menu_entries: Iterable[str],
        title: Optional[str] = None,
        menu_cursor: Optional[str] = DEFAULT_MENU_CURSOR,
        menu_cursor_style: Optional[Iterable[str]] = DEFAULT_MENU_CURSOR_STYLE,
        menu_highlight_style: Optional[Iterable[str]] = DEFAULT_MENU_HIGHLIGHT_STYLE,
        cycle_cursor: bool = DEFAULT_CYCLE_CURSOR,
    ):
        self._fd = sys.stdin.fileno()
        self._menu_entries = tuple(menu_entries)
        self._title = title
        self._menu_cursor = menu_cursor if menu_cursor is not None else ""
        self._menu_cursor_style = tuple(menu_cursor_style) if menu_cursor_style is not None else ()
        self._menu_highlight_style = tuple(menu_highlight_style) if menu_highlight_style is not None else ()
        self._cycle_cursor = cycle_cursor
        self._old_term = None  # type: Optional[List[Union[int, List[bytes]]]]
        self._new_term = None  # type: Optional[List[Union[int, List[bytes]]]]
        self._check_for_valid_styles()
        self._init_terminal_codes()

    @classmethod
    def _init_terminal_codes(cls) -> None:
        if cls._codename_to_terminal_code is not None:
            return
        supported_colors = int(cls._query_terminfo_database("colors"))
        cls._codename_to_terminal_code = {
            codename: cls._query_terminfo_database(codename)
            if not (codename.startswith("bg_") or codename.startswith("fg_")) or supported_colors >= 8
            else ""
            for codename in cls._codenames
        }
        cls._codename_to_terminal_code.update(cls._name_to_control_character)
        cls._terminal_code_to_codename = {
            terminal_code: codename for codename, terminal_code in cls._codename_to_terminal_code.items()
        }

    @classmethod
    def _query_terminfo_database(cls, codename: str) -> str:
        if codename in cls._codename_to_capname:
            capname = cls._codename_to_capname[codename]
        else:
            capname = codename
        try:
            return str(subprocess.check_output(["tput"] + capname.split(), universal_newlines=True))
        except subprocess.CalledProcessError as e:
            # The return code 1 indicates a missing terminal capability
            if e.returncode == 1:
                return ""
            raise e

    def _check_for_valid_styles(self) -> None:
        invalid_styles = []
        for style_tuple in (self._menu_cursor_style, self._menu_highlight_style):
            for style in style_tuple:
                if style not in self._codename_to_capname:
                    invalid_styles.append(style)
        if invalid_styles:
            if len(invalid_styles) == 1:
                raise InvalidStyleError('The style "{}" does not exist.'.format(invalid_styles[0]))
            else:
                raise InvalidStyleError('The styles ("{}") do not exist.'.format('", "'.join(invalid_styles)))

    def _init_term(self) -> None:
        assert self._codename_to_terminal_code is not None
        self._old_term = termios.tcgetattr(self._fd)
        self._new_term = termios.tcgetattr(self._fd)
        self._new_term[3] = cast(int, self._new_term[3]) & ~termios.ICANON & ~termios.ECHO  # unbuffered and no echo
        termios.tcsetattr(self._fd, termios.TCSAFLUSH, self._new_term)
        # Enter terminal application mode to get expected escape codes for arrow keys
        sys.stdout.write(self._codename_to_terminal_code["enter_application_mode"])
        sys.stdout.write(self._codename_to_terminal_code["cursor_invisible"])

    def _reset_term(self) -> None:
        assert self._codename_to_terminal_code is not None
        assert self._old_term is not None
        termios.tcsetattr(self._fd, termios.TCSAFLUSH, self._old_term)
        sys.stdout.write(self._codename_to_terminal_code["cursor_visible"])
        sys.stdout.write(self._codename_to_terminal_code["exit_application_mode"])

    def _read_next_key(self, ignore_case=True):
        code = os.read(self._fd, 80).decode("ascii")  # blocks until any amount of bytes is available
        if code in self._terminal_code_to_codename:
            return self._terminal_code_to_codename[code]
        elif ignore_case:
            return code.lower()
        else:
            return code

    def show(self) -> Optional[int]:
        def print_menu(selected_index: int, with_title=True) -> None:
            if self._title is not None and with_title:
                print(self._title)
            for i, menu_entry in enumerate(self._menu_entries):
                sys.stdout.write(len(self._menu_cursor) * " ")
                if i == selected_index:
                    for style in self._menu_highlight_style:
                        sys.stdout.write(self._codename_to_terminal_code[style])
                sys.stdout.write(menu_entry)
                if i == selected_index:
                    sys.stdout.write(self._codename_to_terminal_code["reset_attributes"])
                if i < len(self._menu_entries) - 1:
                    sys.stdout.write("\n")
            sys.stdout.write("\r" + (len(self._menu_entries) - 1) * self._codename_to_terminal_code["cursor_up"])

        def clear_menu() -> None:
            if self._title is not None:
                sys.stdout.write(
                    self._codename_to_terminal_code["cursor_up"] + self._codename_to_terminal_code["delete_line"]
                )
            sys.stdout.write(len(self._menu_entries) * self._codename_to_terminal_code["delete_line"])
            sys.stdout.flush()

        def position_cursor(selected_index: int) -> None:
            # delete the first column
            sys.stdout.write(
                (len(self._menu_entries) - 1)
                * (len(self._menu_cursor) * " " + "\r" + self._codename_to_terminal_code["cursor_down"])
                + len(self._menu_cursor) * " "
                + "\r"
            )
            sys.stdout.write((len(self._menu_entries) - 1) * self._codename_to_terminal_code["cursor_up"])
            # position cursor and print menu selection character
            sys.stdout.write(selected_index * self._codename_to_terminal_code["cursor_down"])
            for style in self._menu_cursor_style:
                sys.stdout.write(self._codename_to_terminal_code[style])
            sys.stdout.write(self._menu_cursor)
            sys.stdout.write(self._codename_to_terminal_code["reset_attributes"] + "\r")
            sys.stdout.write(selected_index * self._codename_to_terminal_code["cursor_up"])

        assert self._codename_to_terminal_code is not None
        selected_index = 0  # type: Optional[int]
        self._init_term()
        print_menu(selected_index)
        try:
            while True:
                position_cursor(selected_index)
                next_key = self._read_next_key(ignore_case=True)
                if next_key in ("up", "k"):
                    selected_index -= 1
                    if selected_index < 0:
                        if self._cycle_cursor:
                            selected_index = len(self._menu_entries) - 1
                        else:
                            selected_index = 0
                elif next_key in ("down", "j"):
                    selected_index += 1
                    if selected_index >= len(self._menu_entries):
                        if self._cycle_cursor:
                            selected_index = 0
                        else:
                            selected_index = len(self._menu_entries) - 1
                elif next_key in ("enter",):
                    break
                elif next_key in ("escape",):
                    selected_index = None
                    break
                print_menu(selected_index, with_title=False)
        except KeyboardInterrupt:
            selected_index = None
        finally:
            clear_menu()
            self._reset_term()
        return selected_index


class AttributeDict(dict):  # type: ignore
    def __getattr__(self, attr: str) -> Any:
        return self[attr]

    def __setattr__(self, attr: str, value: Any) -> None:
        self[attr] = value


def get_argumentparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
%(prog)s creates simple interactive menus in the terminal and returns the selected entry as exit code.
""",
    )
    parser.add_argument("-t", "--title", action="store", dest="title", help="menu title")
    parser.add_argument(
        "-c",
        "--cursor",
        action="store",
        dest="cursor",
        default=DEFAULT_MENU_CURSOR,
        help="menu cursor (default: %(default)s)",
    )
    parser.add_argument(
        "-s",
        "--cursor_style",
        action="store",
        dest="cursor_style",
        default=",".join(DEFAULT_MENU_CURSOR_STYLE),
        help="style for the menu cursor as comma separated list (default: %(default)s)",
    )
    parser.add_argument(
        "-m",
        "--highlight_style",
        action="store",
        dest="highlight_style",
        default=",".join(DEFAULT_MENU_HIGHLIGHT_STYLE),
        help="style for the selected menu entry as comma separated list (default: %(default)s)",
    )
    parser.add_argument("-C", "--no-cycle", action="store_false", dest="cycle", help="do not cycle the menu selection")
    parser.add_argument(
        "-V", "--version", action="store_true", dest="print_version", help="print the version number and exit"
    )
    parser.add_argument("entries", action="store", nargs="*", help="the menu entries to show")
    return parser


def parse_arguments() -> AttributeDict:
    parser = get_argumentparser()
    args = AttributeDict({key: value for key, value in vars(parser.parse_args()).items()})
    if not args.print_version and not args.entries:
        raise NoMenuEntriesError("No menu entries given!")
    if args.cursor_style != "":
        args.cursor_style = tuple(args.cursor_style.split(","))
    else:
        args.cursor_style = None
    if args.highlight_style != "":
        args.highlight_style = tuple(args.highlight_style.split(","))
    else:
        args.highlight_style = None
    return args


def main() -> None:
    try:
        args = parse_arguments()
    except SystemExit:
        sys.exit(0)  # Error code 0 is the error case in this program
    except NoMenuEntriesError as e:
        print(str(e), file=sys.stderr)
        sys.exit(0)
    if args.print_version:
        print("{}, version {}".format(os.path.basename(sys.argv[0]), __version__))
        sys.exit(0)
    try:
        terminal_menu = TerminalMenu(
            menu_entries=args.entries,
            title=args.title,
            menu_cursor=args.cursor,
            menu_cursor_style=args.cursor_style,
            menu_highlight_style=args.highlight_style,
            cycle_cursor=args.cycle,
        )
    except InvalidStyleError as e:
        print(str(e), file=sys.stderr)
        sys.exit(0)
    chosen_entry = terminal_menu.show()
    if chosen_entry is None:
        sys.exit(0)
    else:
        sys.exit(chosen_entry + 1)


if __name__ == "__main__":
    main()
