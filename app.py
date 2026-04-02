"""
app.py
NBA Betting Assistant — PyQt6 desktop application.

Install:
    pip install PyQt6

Run:
    python app.py
"""

import sys
import os
import json
import webbrowser
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QTabWidget, QFrame, QScrollArea,
    QGridLayout, QSizePolicy, QProgressBar, QSplitter, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QProcess, QTimer, QSize, QPropertyAnimation,
    QEasingCurve
)
from PyQt6.QtGui import (
    QFont, QColor, QPalette, QTextCharFormat, QSyntaxHighlighter,
    QTextCursor, QPainter, QLinearGradient, QBrush, QIcon
)


# ── Theme ─────────────────────────────────────────────────────────────────────
BG         = "#06090f"
SURFACE    = "#0c1220"
SURFACE2   = "#111d2e"
SURFACE3   = "#162336"
BORDER     = "#1c2e42"
BORDER2    = "#243648"

CYAN       = "#00c8f0"
CYAN_DIM   = "#007a94"
AMBER      = "#f0a500"
AMBER_DIM  = "#8a5f00"
GREEN      = "#00e676"
GREEN_DIM  = "#007a40"
RED        = "#ff3d57"
RED_DIM    = "#8a1f2e"
MUTED      = "#3d5a73"
TEXT       = "#d0dde8"
TEXT_DIM   = "#607080"

MONO  = "Consolas"
TITLE = "Consolas"

ET_OFFSET = timedelta(hours=-5)


STYLESHEET = f"""
* {{ box-sizing: border-box; margin: 0; padding: 0; }}

QMainWindow, QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: {MONO};
    font-size: 12px;
}}

/* ── Tabs ── */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    background: {SURFACE};
    border-top: none;
}}
QTabBar::tab {{
    background: {BG};
    color: {MUTED};
    padding: 9px 22px;
    border: 1px solid {BORDER};
    border-bottom: none;
    font-size: 10px;
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background: {SURFACE};
    color: {CYAN};
    border-bottom: 2px solid {CYAN};
}}
QTabBar::tab:hover:!selected {{
    background: {SURFACE2};
    color: {TEXT};
}}

/* ── Buttons ── */
QPushButton {{
    background: transparent;
    color: {CYAN};
    border: 1px solid {CYAN_DIM};
    border-radius: 2px;
    padding: 9px 20px;
    font-family: {MONO};
    font-size: 10px;
    letter-spacing: 2px;
    text-transform: uppercase;
    font-weight: bold;
}}
QPushButton:hover {{
    background: {CYAN};
    color: {BG};
    border-color: {CYAN};
}}
QPushButton:pressed {{
    background: {CYAN_DIM};
    color: {BG};
}}
QPushButton:disabled {{
    background: transparent;
    color: {MUTED};
    border-color: {BORDER};
}}
QPushButton#amber {{
    color: {AMBER};
    border-color: {AMBER_DIM};
}}
QPushButton#amber:hover {{
    background: {AMBER};
    color: {BG};
    border-color: {AMBER};
}}
QPushButton#green {{
    color: {GREEN};
    border-color: {GREEN_DIM};
}}
QPushButton#green:hover {{
    background: {GREEN};
    color: {BG};
    border-color: {GREEN};
}}
QPushButton#red {{
    color: {RED};
    border-color: {RED_DIM};
}}
QPushButton#small {{
    padding: 5px 12px;
    font-size: 9px;
}}

/* ── Text areas ── */
QTextEdit {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 2px;
    font-family: {MONO};
    font-size: 11px;
    padding: 8px;
    selection-background-color: {CYAN};
    selection-color: {BG};
    line-height: 1.4;
}}

/* ── Tables ── */
QTableWidget {{
    background: {SURFACE};
    color: {TEXT};
    border: none;
    font-family: {MONO};
    font-size: 11px;
    gridline-color: {BORDER};
    outline: none;
}}
QTableWidget::item {{
    padding: 6px 10px;
    border-bottom: 1px solid {BORDER};
}}
QTableWidget::item:selected {{
    background: {SURFACE3};
    color: {CYAN};
}}
QHeaderView::section {{
    background: {SURFACE2};
    color: {MUTED};
    padding: 6px 10px;
    border: none;
    border-bottom: 1px solid {BORDER2};
    font-size: 9px;
    letter-spacing: 2px;
    text-transform: uppercase;
    font-weight: bold;
}}

/* ── Scrollbars ── */
QScrollBar:vertical {{
    background: {BG};
    width: 6px;
    border-radius: 3px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER2};
    border-radius: 3px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{
    background: {MUTED};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

/* ── Progress bar ── */
QProgressBar {{
    background: {SURFACE2};
    border: none;
    border-radius: 2px;
    height: 3px;
    text-align: center;
}}
QProgressBar::chunk {{
    background: {CYAN};
    border-radius: 2px;
}}

/* ── Frames ── */
QFrame#card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 2px;
}}
QFrame#divider {{
    background: {BORDER};
    max-height: 1px;
}}
QFrame#accent_bar {{
    background: {CYAN};
    max-height: 2px;
    max-width: 40px;
}}

/* ── Splitter ── */
QSplitter::handle {{
    background: {BORDER};
    width: 1px;
}}
"""


# ── Worker ────────────────────────────────────────────────────────────────────
class Worker(QThread):
    output  = pyqtSignal(str)
    done_ok = pyqtSignal()
    done_err= pyqtSignal(int)

    def __init__(self, script, args=None):
        super().__init__()
        self.script = script
        self.args   = args or []

    @staticmethod
    def _python() -> str:
        """
        Resolves the correct Python executable — prefers the .venv next to app.py
        so subprocesses always use the same environment as the running app.
        Falls back to sys.executable if no venv folder is found.
        """
        app_dir = Path(__file__).parent
        for candidate in [
            app_dir / ".venv" / "Scripts" / "python.exe",
            app_dir / ".venv" / "bin"     / "python",
            app_dir / "venv"  / "Scripts" / "python.exe",
            app_dir / "venv"  / "bin"     / "python",
        ]:
            if candidate.exists():
                return str(candidate)
        return sys.executable

    def run(self):
        p = QProcess()
        p.setWorkingDirectory(str(Path(__file__).parent))
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)

        # Force UTF-8 output so Unicode symbols (⚠ ─ ✓ ✗) render correctly
        env = p.processEnvironment()
        from PyQt6.QtCore import QProcessEnvironment
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUTF8", "1")
        p.setProcessEnvironment(env)
        p.readyRead.connect(
            lambda: self.output.emit(
                bytes(p.readAll()).decode("utf-8", errors="replace")
            )
        )
        p.start(self._python(), [self.script] + self.args)
        p.waitForFinished(-1)
        if p.exitCode() == 0:
            self.done_ok.emit()
        else:
            self.done_err.emit(p.exitCode())


# ── Console widget ────────────────────────────────────────────────────────────
class Console(QTextEdit):
    COLOURS = {
        "win":    QColor(GREEN),
        "lose":   QColor(RED),
        "head":   QColor(CYAN),
        "warn":   QColor(AMBER),
        "muted":  QColor(MUTED),
        "normal": QColor(TEXT),
    }

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setFont(QFont(MONO, 11))

    def _classify(self, line: str) -> str:
        t = line.strip()
        if any(x in t for x in ["WON", "✓", "CORRECT", "✅", "successfully"]):
            return "win"
        if any(x in t for x in ["LOST", "✗", "ERROR", "FAILED", "error"]):
            return "lose"
        if any(x in t for x in ["===", "───", "Step ", "Phase ", "Pre-", "Pre "]):
            return "head"
        if any(x in t for x in ["WARNING", "warning", "pending", "⚠"]):
            return "warn"
        if any(x in t for x in ["risk", "adj:", "Implied:"]):
            return "muted"
        return "normal"

    def append_text(self, text: str):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for line in text.splitlines(keepends=True):
            fmt = QTextCharFormat()
            fmt.setForeground(self.COLOURS[self._classify(line)])
            cursor.insertText(line, fmt)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def clear_log(self):
        self.clear()
        ts = datetime.now().strftime("%H:%M:%S")
        self.append_text(f"── Console cleared {ts} ──\n")


# ── Stat card ─────────────────────────────────────────────────────────────────
class StatCard(QFrame):
    def __init__(self, label: str, value: str = "—", color: str = CYAN):
        super().__init__()
        self.setObjectName("card")
        self.setMinimumWidth(120)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(5)

        self._lbl = QLabel(label.upper())
        self._lbl.setFont(QFont(MONO, 8))
        self._lbl.setStyleSheet(f"color: {MUTED}; letter-spacing: 2px;")

        self._val = QLabel(value)
        self._val.setFont(QFont(TITLE, 24, QFont.Weight.Bold))
        self._val.setStyleSheet(f"color: {color};")

        lay.addWidget(self._lbl)
        lay.addWidget(self._val)

    def set(self, value: str, color: str = None):
        self._val.setText(value)
        if color:
            self._val.setStyleSheet(f"color: {color};")


# ── Main window ───────────────────────────────────────────────────────────────
class App(QMainWindow):
    APP_DIR = Path(__file__).parent
    DATA_DIR = APP_DIR / "data"

    def __init__(self):
        super().__init__()
        self.worker = None
        self._setup()
        self._build()
        self._refresh_stats()

        # Clock timer
        t = QTimer(self)
        t.timeout.connect(self._tick)
        t.start(1000)
        self._tick()

    def _setup(self):
        self.setWindowTitle("NBA BETTING ASSISTANT")
        self.setMinimumSize(1200, 740)
        self.resize(1400, 820)

    # ── UI construction ───────────────────────────────────────────────────────
    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(24, 18, 24, 14)
        lay.setSpacing(14)

        lay.addLayout(self._header())
        lay.addWidget(self._divider())
        lay.addWidget(self._tabs(), stretch=1)
        lay.addLayout(self._statusbar())

    def _header(self) -> QHBoxLayout:
        h = QHBoxLayout()
        h.setSpacing(0)

        left = QVBoxLayout()
        left.setSpacing(3)

        title = QLabel("NBA BETTING ASSISTANT")
        title.setFont(QFont(TITLE, 20, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {CYAN}; letter-spacing: 4px;")

        sub = QLabel("DRY RUN  ·  PINNACLE REFERENCE  ·  QUARTER-KELLY SIZING")
        sub.setFont(QFont(MONO, 9))
        sub.setStyleSheet(f"color: {MUTED}; letter-spacing: 2px;")

        left.addWidget(title)
        left.addWidget(sub)

        self._clock = QLabel()
        self._clock.setFont(QFont(MONO, 10))
        self._clock.setStyleSheet(f"color: {TEXT_DIM};")
        self._clock.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        h.addLayout(left)
        h.addStretch()
        h.addWidget(self._clock)
        return h

    def _divider(self) -> QFrame:
        f = QFrame()
        f.setObjectName("divider")
        f.setFixedHeight(1)
        f.setStyleSheet(f"background: {BORDER};")
        return f

    def _tabs(self) -> QTabWidget:
        self._tab_widget = QTabWidget()

        self._tab_widget.addTab(self._tab_today(),     "TODAY'S PICKS")
        self._tab_widget.addTab(self._tab_yesterday(), "YESTERDAY'S RESULTS")
        self._tab_widget.addTab(self._tab_console(),   "CONSOLE")

        return self._tab_widget

    # ── TODAY tab ─────────────────────────────────────────────────────────────
    def _tab_today(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 14, 0, 0)
        lay.setSpacing(14)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._btn_picks   = QPushButton("⚡  GET TODAY'S PICKS")
        self._btn_settle  = QPushButton("◈  SETTLE YESTERDAY")
        self._btn_dash    = QPushButton("◉  OPEN DASHBOARD")
        self._btn_morning = QPushButton("▶  FULL MORNING RUN")

        self._btn_settle.setObjectName("amber")
        self._btn_dash.setObjectName("green")

        for btn in [self._btn_picks, self._btn_settle,
                    self._btn_dash, self._btn_morning]:
            btn.setMinimumHeight(42)
            btn_row.addWidget(btn)

        self._btn_picks.clicked.connect(self._run_picks)
        self._btn_settle.clicked.connect(self._run_settle)
        self._btn_dash.clicked.connect(self._run_dashboard)
        self._btn_morning.clicked.connect(self._run_morning)

        lay.addLayout(btn_row)
        lay.addWidget(self._build_stats_panel())
        return w

    def _build_stats_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        # Stat cards
        card_row = QHBoxLayout()
        card_row.setSpacing(10)
        self._c_games  = StatCard("Games")
        self._c_h2h    = StatCard("H2H Acc")
        self._c_spread = StatCard("Spread", color=AMBER)
        self._c_totals = StatCard("Totals", color=AMBER)
        self._c_vb     = StatCard("VB Record")
        self._c_roi    = StatCard("ROI",    color=GREEN)
        self._c_pnl    = StatCard("P&L",    color=GREEN)

        for c in [self._c_games, self._c_h2h, self._c_spread,
                  self._c_totals, self._c_vb, self._c_roi, self._c_pnl]:
            card_row.addWidget(c)
        lay.addLayout(card_row)

        # Recent value bets table
        tframe = QFrame()
        tframe.setObjectName("card")
        tlay = QVBoxLayout(tframe)
        tlay.setContentsMargins(14, 10, 14, 10)
        tlay.setSpacing(8)

        hdr = QLabel("RECENT VALUE BETS")
        hdr.setFont(QFont(MONO, 8))
        hdr.setStyleSheet(f"color: {MUTED}; letter-spacing: 2px;")
        tlay.addWidget(hdr)

        self._vb_table = QTableWidget()
        self._vb_table.setColumnCount(6)
        self._vb_table.setHorizontalHeaderLabels(
            ["Date", "Market", "Bet", "Odds", "Outcome", "P&L"]
        )
        self._vb_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._vb_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._vb_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._vb_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._vb_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._vb_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self._vb_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._vb_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._vb_table.verticalHeader().setVisible(False)
        self._vb_table.setShowGrid(False)
        self._vb_table.setMaximumHeight(260)
        tlay.addWidget(self._vb_table)
        lay.addWidget(tframe)

        return w

    # ── YESTERDAY tab ─────────────────────────────────────────────────────────
    def _tab_yesterday(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 14, 0, 0)
        lay.setSpacing(12)

        btn_row = QHBoxLayout()
        self._btn_load_results = QPushButton("◈  LOAD YESTERDAY'S RESULTS")
        self._btn_load_results.setObjectName("amber")
        self._btn_load_results.setMinimumHeight(42)
        self._btn_load_results.clicked.connect(self._show_yesterday)
        btn_row.addWidget(self._btn_load_results)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._results_console = Console()
        lay.addWidget(self._results_console)
        return w

    # ── CONSOLE tab ───────────────────────────────────────────────────────────
    def _tab_console(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 14, 0, 0)
        lay.setSpacing(10)

        btn_row = QHBoxLayout()
        btn_clear = QPushButton("CLEAR")
        btn_clear.setObjectName("small")
        btn_clear.setMaximumWidth(80)
        btn_clear.clicked.connect(lambda: self.console.clear_log())
        btn_row.addStretch()
        btn_row.addWidget(btn_clear)
        lay.addLayout(btn_row)

        self.console = Console()
        lay.addWidget(self.console)
        return w

    def _statusbar(self) -> QHBoxLayout:
        h = QHBoxLayout()
        self._status = QLabel("READY")
        self._status.setFont(QFont(MONO, 10))
        self._status.setStyleSheet(f"color: {MUTED};")

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._progress.setMaximumWidth(160)
        self._progress.setFixedHeight(3)

        h.addWidget(self._status)
        h.addStretch()
        h.addWidget(self._progress)
        return h

    # ── Actions ───────────────────────────────────────────────────────────────
    def _run_picks(self):
        self._launch("dry_run.py", "Fetching today's picks…", self._on_picks_done)

    def _run_settle(self):
        self._launch("results_tracker.py", "Settling yesterday…", self._on_settle_done)

    def _run_dashboard(self):
        self._launch("dashboard.py", "Generating dashboard…", self._on_dash_done)

    def _run_morning(self):
        self._launch("results_tracker.py", "Running morning sequence…",
                     lambda ok: self._chain_morning_2(ok))

    def _chain_morning_2(self, ok):
        if ok:
            self._launch("report.py", "Generating report…",
                         lambda ok2: self._chain_morning_3(ok2))

    def _chain_morning_3(self, ok):
        if ok:
            self._launch("dashboard.py", "Updating dashboard…",
                         lambda ok2: self._chain_morning_4(ok2))

    def _chain_morning_4(self, ok):
        if ok:
            self._launch("dry_run.py", "Fetching today's picks…",
                         self._on_picks_done)

    def _show_yesterday(self):
        yesterday = (datetime.now(timezone.utc) + ET_OFFSET - timedelta(days=1)).date().isoformat()
        path = self.DATA_DIR / f"picks_{yesterday}.json"

        c = self._results_console
        c.clear()

        if not path.exists():
            c.append_text(f"No picks file found for {yesterday}.\nRun 'Settle Yesterday' first.\n")
            return

        with open(path) as f:
            picks = json.load(f)

        s = picks.get("results_summary")
        if not s:
            c.append_text(f"Results for {yesterday} not yet settled.\nClick 'Settle Yesterday' first.\n")
            return

        n = s.get("settled", 0)
        c.append_text(f"{'='*62}\n  RESULTS — {yesterday}  ({n} games settled)\n{'='*62}\n\n")

        if n:
            c.append_text(f"  Market accuracy:\n")
            c.append_text(f"    H2H:    {s['h2h_correct']}/{n}  ({s['h2h_correct']/n*100:.1f}%)\n")
            c.append_text(f"    Spread: {s['spread_correct']}/{n}  ({s['spread_correct']/n*100:.1f}%)\n")
            c.append_text(f"    Totals: {s['total_correct']}/{n}  ({s['total_correct']/n*100:.1f}%)\n\n")

        c.append_text("  Game-by-game:\n")
        for pred in picks.get("predictions", []):
            if not pred.get("h2h_actual_winner"):
                continue
            icon = "✓" if pred.get("h2h_correct") else "✗"
            c.append_text(f"    {icon} {pred['away_team']} @ {pred['home_team']}\n")
            c.append_text(f"      H2H: {pred['h2h_predicted_winner']:22s} → {pred['h2h_actual_winner']}\n")
            if pred.get("spread_actual_margin") is not None:
                si = "✓" if pred.get("spread_correct") else "✗"
                c.append_text(f"      {si} Spread: actual {pred['spread_actual_margin']:+.0f}\n")
            if pred.get("total_actual") is not None:
                ti = "✓" if pred.get("total_correct") else "✗"
                c.append_text(f"      {ti} Total:  actual {pred['total_actual']:.0f}\n")

        c.append_text("\n  Value bets:\n")
        vb_won = vb_lost = 0
        for vb in picks.get("value_bets", []):
            if not vb.get("outcome"):
                continue
            icon   = "✓" if vb["outcome"] == "won" else "✗"
            market = vb["market"].upper()
            c.append_text(
                f"    {icon} [{market:6s}] {vb['bet_label']:30s} "
                f"{vb['outcome'].upper():4s}  €{vb['actual_pnl']:+.2f}\n"
            )
            if vb["outcome"] == "won": vb_won += 1
            else:                      vb_lost += 1

        staked = s.get("value_bets_staked", 0.0)
        pnl    = s.get("value_bets_pnl", 0.0)
        roi    = pnl / staked * 100 if staked else 0
        c.append_text(
            f"\n    Record: {vb_won}W / {vb_lost}L  ·  "
            f"Staked: €{staked:.2f}  ·  P&L: €{pnl:+.2f}  ·  ROI: {roi:+.1f}%\n"
        )

        settled_props = [pb for pb in picks.get("prop_bets", []) if pb.get("outcome")]
        if settled_props:
            c.append_text("\n  Player props:\n")
            pw = pl = 0
            for pb in settled_props:
                icon = "✓" if pb["outcome"] == "won" else "✗"
                c.append_text(
                    f"    {icon} {pb['bet_label']:38s} "
                    f"actual: {pb['actual_value']:.1f}  €{pb['actual_pnl']:+.2f}\n"
                )
                if pb["outcome"] == "won": pw += 1
                else:                      pl += 1
            prop_staked = s.get("prop_bets_staked", 0.0)
            prop_pnl    = s.get("prop_bets_pnl", 0.0)
            prop_roi    = prop_pnl / prop_staked * 100 if prop_staked else 0
            c.append_text(
                f"\n    Props record: {pw}W / {pl}L  ·  "
                f"P&L: €{prop_pnl:+.2f}  ·  ROI: {prop_roi:+.1f}%\n"
            )

        c.append_text(f"\n{'='*62}\n")
        self._tab_widget.setCurrentIndex(1)

    # ── Worker plumbing ───────────────────────────────────────────────────────
    def _launch(self, script: str, label: str, on_done=None, args=None):
        if self.worker and self.worker.isRunning():
            return
        self._set_btns(False)
        self._set_status(label.upper(), MUTED)
        self._progress.setVisible(True)
        self.console.append_text(f"\n── {label} ──\n")
        self._tab_widget.setCurrentIndex(2)

        self.worker = Worker(script, args)
        self.worker.output.connect(self.console.append_text)
        self.worker.done_ok.connect(lambda: self._on_done(True,  on_done))
        self.worker.done_err.connect(lambda c: self._on_done(False, on_done))
        self.worker.start()

    def _on_done(self, ok: bool, callback):
        self._set_btns(True)
        self._progress.setVisible(False)
        if ok:
            self._set_status("DONE", GREEN)
            self.console.append_text("\n── Completed successfully ──\n")
        else:
            self._set_status("ERROR", RED)
            self.console.append_text("\n── Completed with errors ──\n")
        QTimer.singleShot(3000, lambda: self._set_status("READY", MUTED))
        if callback:
            callback(ok)

    def _on_picks_done(self, ok: bool):
        if ok:
            self._refresh_stats()
            self._tab_widget.setCurrentIndex(0)

    def _on_settle_done(self, ok: bool):
        if ok:
            self._refresh_stats()
            self._show_yesterday()

    def _on_dash_done(self, ok: bool):
        if ok:
            path = self.APP_DIR / "dashboard.html"
            if path.exists():
                webbrowser.open(path.as_uri())

    def _set_btns(self, enabled: bool):
        for b in [self._btn_picks, self._btn_settle,
                  self._btn_dash, self._btn_morning,
                  self._btn_load_results]:
            b.setEnabled(enabled)

    def _set_status(self, text: str, color: str):
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color};")

    # ── Stats refresh ─────────────────────────────────────────────────────────
    def _refresh_stats(self):
        files = sorted(self.DATA_DIR.glob("picks_*.json"))
        settled = []
        for f in files:
            try:
                with open(f) as fp:
                    p = json.load(fp)
                if p.get("results_summary"):
                    settled.append(p)
            except Exception:
                pass

        if not settled:
            return

        games = h2h_c = h2h_t = sp_c = sp_t = tot_c = tot_t = 0
        vb_won = vb_total = 0
        staked = pnl = 0.0
        all_vb = []

        for p in settled:
            s = p["results_summary"]
            n = s.get("settled", 0)
            games  += n
            h2h_c  += s.get("h2h_correct", 0)
            h2h_t  += n
            sp_c   += s.get("spread_correct", 0)
            sp_t   += n
            tot_c  += s.get("total_correct", 0)
            tot_t  += n
            vb_won += s.get("value_bets_won", 0)
            vb_total+= s.get("value_bets_total", 0)
            staked += s.get("value_bets_staked", s.get("total_staked", 0.0))
            pnl    += s.get("value_bets_pnl", s.get("total_pnl", 0.0))
            for vb in p.get("value_bets", []):
                if vb.get("outcome"):
                    all_vb.append({**vb, "date": p["date"]})

        def pct(w, t): return f"{w/t*100:.1f}%" if t else "—"
        def roi_str(p, s): return f"{p/s*100:+.1f}%" if s else "—"

        pnl_col = GREEN if pnl >= 0 else RED
        roi_col = GREEN if pnl >= 0 else RED

        self._c_games.set(str(games))
        self._c_h2h.set(pct(h2h_c, h2h_t))
        self._c_spread.set(pct(sp_c, sp_t))
        self._c_totals.set(pct(tot_c, tot_t))
        self._c_vb.set(f"{vb_won}/{vb_total}")
        self._c_roi.set(roi_str(pnl, staked), roi_col)
        self._c_pnl.set(f"€{pnl:+.0f}", pnl_col)

        # Fill value bets table (most recent 25)
        recent = list(reversed(all_vb[-25:]))
        self._vb_table.setRowCount(len(recent))
        for row, vb in enumerate(recent):
            won  = vb.get("outcome") == "won"
            col  = GREEN if won else RED
            cols = [
                vb.get("date", ""),
                vb.get("market", "").upper(),
                vb.get("bet_label", ""),
                str(vb.get("best_odds", "")),
                ("✓ WON" if won else "✗ LOST"),
                f"€{vb['actual_pnl']:+.2f}",
            ]
            for c, val in enumerate(cols):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter |
                    (Qt.AlignmentFlag.AlignRight if c in (3, 5) else Qt.AlignmentFlag.AlignLeft))
                if c in (4, 5):
                    item.setForeground(QColor(col))
                self._vb_table.setItem(row, c, item)

    # ── Clock ─────────────────────────────────────────────────────────────────
    def _tick(self):
        now = datetime.now()
        et  = datetime.now(timezone.utc) + ET_OFFSET
        self._clock.setText(
            f"{now.strftime('%a %d %b %Y  %H:%M:%S')}  ·  ET {et.strftime('%H:%M')}"
        )


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    app.setStyle("Fusion")
    window = App()
    window.show()
    sys.exit(app.exec())
