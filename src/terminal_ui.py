"""Small dependency-free terminal renderer; safe for redirected output."""
import os
import re
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo


def plain(value):
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "", str(value))


def color(value, code="36"):
    value = plain(value)
    if sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb":
        return f"\033[{code}m{value}\033[0m"
    return value


def brand():
    """Six letters in the logo palette; plain text when colors are disabled."""
    palette = ('38b6ff', 'ffc2c2', 'ffcd1a', '73be00', 'e2a9f1', 'ff5757')
    return ''.join(color(letter, '1;38;2;' + ';'.join(str(int(rgb[i:i+2], 16))
                   for i in (0, 2, 4))) for letter, rgb in zip('refill', palette))


def heading(title, subtitle=None):
    print(brand() + " / " + plain(title))
    if subtitle:
        print(plain(subtitle))


def section(title, detail=None):
    suffix = color(f' ({detail})', '2') if detail else ''
    print("\n" + color(title.upper(), "1") + suffix)


def field(label, value, tone=None):
    print(f"  {label:<18} {color(value, tone) if tone else plain(value)}")


def flag(value, yes="Enabled", no="Disabled"):
    return yes if value is True else no if value is False else "Unknown"


def local_zone():
    """Read the OS timezone on every call, including future DST rules."""
    path = Path('/etc/localtime')
    resolved = str(path.resolve())
    name = resolved.split('/zoneinfo/', 1)[-1] if '/zoneinfo/' in resolved else 'Local time'
    with path.open('rb') as source:
        return ZoneInfo.from_file(source, key=name)


def timestamp(value, compact=False):
    if value is None:
        return "Unknown"
    zone = local_zone()
    local = datetime.fromtimestamp(value, zone)
    if compact:
        return local.strftime('%d.%m.%y %H:%M:%S')
    offset = local.strftime('%z')
    offset = offset[:3] + ':' + offset[3:]
    return local.strftime('%d.%m.%y %H:%M:%S') + f' ({zone.key}, UTC{offset})'


def table(headers, rows, tones=None):
    rows = [[plain(cell) for cell in row] for row in rows]
    widths = [max([len(header)] + [len(row[i]) for row in rows]) for i, header in enumerate(headers)]
    def line(row):
        return "  " + "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip()
    print(color(line(headers), "1"))
    print(line(["-" * width for width in widths]))
    for row in rows:
        if tones:
            print('  ' + '  '.join(color(cell.ljust(width), tones.get(i, '0'))
                                  for i, (cell, width) in enumerate(zip(row, widths))).rstrip())
        else:
            print(line(row))


def progress(percent, width=16):
    if not isinstance(percent, (int, float)):
        return "Unknown"
    filled = round(max(0, min(100, percent)) / 100 * width)
    return "[" + "#" * filled + "-" * (width-filled) + f"] {percent:g}%"
