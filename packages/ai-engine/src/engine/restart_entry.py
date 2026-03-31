from __future__ import annotations

import re

NAMED_RESTART_ENTRY_RE = re.compile(
    r"""
    (?:
        function\s+
      | (?:const|let|var)\s+
    )?
    (?:
        restart\w*
      | reset\w*
      | startgame
      | newgame
      | initgame
      | begin\w*game
      | play\w*game
      | resume\w*game
      | boot\w*game
    )
    \s*
    (?:=|\()
    """,
    re.IGNORECASE | re.VERBOSE,
)

TERMINAL_BRANCH_RESTART_RE = re.compile(
    r"""
    if\s*\([^)]*
    (?:game_over|gameover|game\ over|level_complete|levelcomplete|completed|complete|victory|win|won|clear)
    [^)]*\)
    \s*\{?
    [\s\S]{0,260}?
    (?:
        restart\w*\s*\(
      | reset\w*\s*\(
      | startgame\s*\(
      | newgame\s*\(
      | initgame\s*\(
      | begin\w*game\s*\(
      | play\w*game\s*\(
      | resume\w*game\s*\(
      | boot\w*game\s*\(
      | boot\s*\(\)\s*;?\s*init\s*\(
      | init\s*\(
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

TERMINAL_READY_RESET_RE = re.compile(
    r"""
    if\s*\([^)]*
    (?:game_over|gameover|game\ over|level_complete|levelcomplete|completed|complete|victory|win|won|clear)
    [^)]*\)
    \s*\{?
    [\s\S]{0,320}?
    (?:
        (?:state|gameState|currentState|status|gameStatus)\s*=\s*['\"]?(?:ready|start)['\"]?
      | (?:gameOver|game_over|isOver|isGameOver)\s*=\s*false
    )
    [\s\S]{0,220}?
    (?:
        create\s*\(
      | init\s*\(
      | setup\w*\s*\(
      | build\w*\s*\(
      | seed\w*\s*\(
      | reset\w*\s*\(
      | restart\w*\s*\(
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

RESTART_TEXT_HINT_RE = re.compile(
    r"(?:重新开始|再来一局|点击重新开始|play again|restart|tap to retry|click to retry)",
    re.IGNORECASE,
)

TEXT_HINT_TRIGGER_RE = re.compile(
    r"""
    (?:重新开始|再来一局|点击重新开始|play\ again|restart)
    [\s\S]{0,420}?
    (?:
        addEventListener
      | onclick
      | onpointerdown
      | ontouchstart
      | keydown
    )
    [\s\S]{0,420}?
    (?:
        restart\w*\s*\(
      | reset\w*\s*\(
      | startgame\s*\(
      | newgame\s*\(
      | initgame\s*\(
      | begin\w*game\s*\(
      | play\w*game\s*\(
      | resume\w*game\s*\(
      | boot\w*game\s*\(
      | boot\s*\(\)\s*;?\s*init\s*\(
      | init\s*\(
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def has_restart_entry(code: str) -> bool:
    source = code or ""
    if not source:
        return False

    if NAMED_RESTART_ENTRY_RE.search(source):
        return True

    if TERMINAL_BRANCH_RESTART_RE.search(source):
        return True

    if TERMINAL_READY_RESET_RE.search(source):
        return True

    if RESTART_TEXT_HINT_RE.search(source) and TEXT_HINT_TRIGGER_RE.search(source):
        return True

    return False
