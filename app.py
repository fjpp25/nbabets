"""
app.py — NBA Betting Assistant (PyQt6)

Install:
    pip install PyQt6 PyQt6-WebEngine

Run:
    python app.py
"""

import sys, os, json, webbrowser, subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QTabWidget, QFrame, QScrollArea,
    QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QDialog, QDoubleSpinBox, QDialogButtonBox,
    QSizePolicy, QSplitter, QStackedWidget
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QProcess, QTimer, QProcessEnvironment, QUrl
from PyQt6.QtGui import QFont, QColor, QTextCharFormat, QTextCursor

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except ImportError:
    HAS_WEBENGINE = False

# ── Palette ───────────────────────────────────────────────────────────────────
BG       = "#06090f"
SURF     = "#0c1220"
SURF2    = "#111d2e"
SURF3    = "#162336"
BORDER   = "#1c2e42"
BORDER2  = "#243648"
CYAN     = "#00c8f0"
CYAN_D   = "#007a94"
AMBER    = "#f0a500"
AMBER_D  = "#8a5f00"
GREEN    = "#00e676"
GREEN_D  = "#007a40"
RED      = "#ff3d57"
RED_D    = "#8a1f2e"
PURPLE   = "#b388ff"
PURPLE_D = "#6a3fb5"
MUTED    = "#3d5a73"
TEXT     = "#d0dde8"
TEXT_D   = "#607080"
MONO     = "Consolas"
ET       = timedelta(hours=-5)

CATEGORY = {
    "h2h":    (CYAN,   "MONEYLINE"),
    "spread": (AMBER,  "SPREAD / HANDICAP"),
    "totals": (PURPLE, "OVER / UNDER"),
    "props":  (GREEN,  "PLAYER PROPS"),
}

QSS = f"""
QMainWindow, QWidget {{ background:{BG}; color:{TEXT}; font-family:{MONO}; font-size:12px; }}
QTabWidget::pane {{ border:1px solid {BORDER}; background:{SURF}; border-top:none; }}
QTabBar::tab {{ background:{BG}; color:{MUTED}; padding:10px 26px; border:1px solid {BORDER};
    border-bottom:none; font-size:10px; letter-spacing:2px; margin-right:2px; }}
QTabBar::tab:selected {{ background:{SURF}; color:{CYAN}; border-bottom:2px solid {CYAN}; }}
QTabBar::tab:hover:!selected {{ background:{SURF2}; color:{TEXT}; }}
QPushButton {{ background:transparent; color:{CYAN}; border:1px solid {CYAN_D}; border-radius:2px;
    padding:10px 22px; font-family:{MONO}; font-size:10px; letter-spacing:2px; font-weight:bold; }}
QPushButton:hover {{ background:{CYAN}; color:{BG}; border-color:{CYAN}; }}
QPushButton:pressed {{ background:{CYAN_D}; color:{BG}; }}
QPushButton:disabled {{ background:transparent; color:{MUTED}; border-color:{BORDER}; }}
QPushButton#amber {{ color:{AMBER}; border-color:{AMBER_D}; }}
QPushButton#amber:hover {{ background:{AMBER}; color:{BG}; border-color:{AMBER}; }}
QPushButton#green {{ color:{GREEN}; border-color:{GREEN_D}; }}
QPushButton#green:hover {{ background:{GREEN}; color:{BG}; border-color:{GREEN}; }}
QPushButton#placed {{ background:{GREEN_D}; color:{GREEN}; border:1px solid {GREEN};
    border-radius:2px; padding:6px 14px; font-size:9px; letter-spacing:1px; font-weight:bold; }}
QPushButton#placed:hover {{ background:{GREEN}; color:{BG}; }}
QPushButton#unplaced {{ background:transparent; color:{MUTED}; border:1px solid {BORDER};
    border-radius:2px; padding:6px 14px; font-size:9px; letter-spacing:1px; }}
QPushButton#unplaced:hover {{ background:{SURF3}; color:{TEXT}; border-color:{TEXT_D}; }}
QPushButton#small {{ padding:5px 12px; font-size:9px; }}
QTextEdit {{ background:{SURF}; color:{TEXT}; border:1px solid {BORDER}; border-radius:2px;
    font-family:{MONO}; font-size:11px; padding:8px; }}
QTableWidget {{ background:{SURF}; color:{TEXT}; border:none; font-family:{MONO};
    font-size:11px; gridline-color:{BORDER}; outline:none; }}
QTableWidget::item {{ padding:7px 10px; border-bottom:1px solid {BORDER}; }}
QTableWidget::item:selected {{ background:{SURF3}; color:{CYAN}; }}
QHeaderView::section {{ background:{SURF2}; color:{MUTED}; padding:7px 10px; border:none;
    border-bottom:1px solid {BORDER2}; font-size:9px; letter-spacing:2px; font-weight:bold; }}
QScrollBar:vertical {{ background:{BG}; width:6px; border-radius:3px; }}
QScrollBar::handle:vertical {{ background:{BORDER2}; border-radius:3px; min-height:20px; }}
QScrollBar::handle:vertical:hover {{ background:{MUTED}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
QProgressBar {{ background:{SURF2}; border:1px solid {BORDER}; border-radius:3px; text-align:center;
    font-family:{MONO}; font-size:10px; color:{MUTED}; }}
QProgressBar::chunk {{ background:{CYAN}; border-radius:3px; }}
QFrame#card {{ background:{SURF}; border:1px solid {BORDER}; border-radius:2px; }}
QScrollArea {{ border:none; background:transparent; }}
QDoubleSpinBox {{ background:{SURF2}; color:{TEXT}; border:1px solid {BORDER2};
    border-radius:2px; padding:4px 8px; font-family:{MONO}; font-size:12px; }}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width:16px; background:{SURF3}; border:none; }}
"""


# ── Worker ────────────────────────────────────────────────────────────────────
class Worker(QThread):
    output   = pyqtSignal(str)
    done_ok  = pyqtSignal()
    done_err = pyqtSignal(int)

    def __init__(self, script, args=None):
        super().__init__()
        self.script = script
        self.args   = args or []

    @staticmethod
    def _python():
        base = Path(__file__).parent
        for c in [base/".venv"/"Scripts"/"python.exe",
                  base/".venv"/"bin"/"python",
                  base/"venv"/"Scripts"/"python.exe",
                  base/"venv"/"bin"/"python"]:
            if c.exists(): return str(c)
        return sys.executable

    def run(self):
        p = QProcess()
        p.setWorkingDirectory(str(Path(__file__).parent))
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUTF8", "1")
        p.setProcessEnvironment(env)
        p.readyRead.connect(
            lambda: self.output.emit(
                bytes(p.readAll()).decode("utf-8", errors="replace")))
        p.start(self._python(), [self.script] + self.args)
        p.waitForFinished(-1)
        if p.exitCode() == 0: self.done_ok.emit()
        else:                  self.done_err.emit(p.exitCode())


# ── Console ───────────────────────────────────────────────────────────────────
class Console(QTextEdit):
    _C = {"win": QColor(GREEN), "lose": QColor(RED), "head": QColor(CYAN),
          "warn": QColor(AMBER), "muted": QColor(MUTED), "normal": QColor(TEXT)}

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setFont(QFont(MONO, 11))

    def _kind(self, t):
        if any(x in t for x in ["WON","✓","successfully"]): return "win"
        if any(x in t for x in ["LOST","✗","ERROR","FAILED"]): return "lose"
        if any(x in t for x in ["===","───","Step ","Phase ","Pre-"]): return "head"
        if any(x in t for x in ["WARNING","warning","pending","⚠"]): return "warn"
        if any(x in t for x in ["risk","adj:","Implied:"]): return "muted"
        return "normal"

    def append_text(self, text):
        c = self.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        for line in text.splitlines(keepends=True):
            fmt = QTextCharFormat()
            fmt.setForeground(self._C[self._kind(line.strip())])
            c.insertText(line, fmt)
        self.setTextCursor(c)
        self.ensureCursorVisible()

    def clear_log(self):
        self.clear()
        self.append_text(f"── cleared {datetime.now().strftime('%H:%M:%S')} ──\n")


# ── Stat card ─────────────────────────────────────────────────────────────────
class StatCard(QFrame):
    def __init__(self, label, value="—", color=CYAN):
        super().__init__()
        self.setObjectName("card")
        self.setMinimumWidth(110)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 11, 14, 11)
        lay.setSpacing(4)
        self._lbl = QLabel(label.upper())
        self._lbl.setFont(QFont(MONO, 8))
        self._lbl.setStyleSheet(f"color:{MUTED}; letter-spacing:2px;")
        self._val = QLabel(value)
        self._val.setFont(QFont(MONO, 22, QFont.Weight.Bold))
        self._val.setStyleSheet(f"color:{color};")
        lay.addWidget(self._lbl)
        lay.addWidget(self._val)

    def set(self, value, color=None):
        self._val.setText(value)
        if color: self._val.setStyleSheet(f"color:{color};")


# ── Stake dialog ──────────────────────────────────────────────────────────────
class StakeDialog(QDialog):
    def __init__(self, bet_label, kelly_stake, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Place Bet")
        self.setMinimumWidth(400)
        self.setStyleSheet(f"background:{SURF}; color:{TEXT}; font-family:{MONO};")
        lay = QVBoxLayout(self)
        lay.setSpacing(16)
        lay.setContentsMargins(24, 24, 24, 24)

        lbl = QLabel(bet_label)
        lbl.setFont(QFont(MONO, 11, QFont.Weight.Bold))
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color:{CYAN};")
        lay.addWidget(lbl)

        sug = QLabel(f"Kelly suggested stake:  €{kelly_stake:.2f}")
        sug.setFont(QFont(MONO, 10))
        sug.setStyleSheet(f"color:{MUTED};")
        lay.addWidget(sug)

        row = QHBoxLayout()
        row.addWidget(QLabel("Your stake (€):"))
        self._spin = QDoubleSpinBox()
        self._spin.setRange(0.01, 100000.0)
        self._spin.setDecimals(2)
        self._spin.setSingleStep(1.0)
        self._spin.setValue(kelly_stake)
        self._spin.setMinimumWidth(130)
        row.addStretch()
        row.addWidget(self._spin)
        lay.addLayout(row)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        btns.setStyleSheet(f"""
            QPushButton {{ background:transparent; color:{CYAN}; border:1px solid {CYAN_D};
                border-radius:2px; padding:7px 18px; font-family:{MONO};
                font-size:10px; letter-spacing:1px; }}
            QPushButton:hover {{ background:{CYAN}; color:{BG}; }}
        """)
        lay.addWidget(btns)

    @property
    def stake(self): return self._spin.value()


# ── Bet card ──────────────────────────────────────────────────────────────────
class BetCard(QFrame):
    placed_changed = pyqtSignal(str, bool, float)

    def __init__(self, bet, bet_id, category, parent=None):
        super().__init__(parent)
        self.bet     = bet
        self.bet_id  = bet_id
        self.cat     = category
        self._placed = bet.get("placed", False)
        self._stake  = bet.get("placed_stake", bet.get("simulated_stake", 10.0))
        color, _     = CATEGORY.get(category, (CYAN, ""))

        self.setObjectName("card")
        self.setStyleSheet(f"QFrame#card {{ border-left: 3px solid {color}; }}")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 11, 16, 11)
        lay.setSpacing(14)

        # Info column
        info = QVBoxLayout()
        info.setSpacing(4)

        top = QHBoxLayout()
        game = QLabel(bet.get("game", bet.get("player", "")))
        game.setFont(QFont(MONO, 11, QFont.Weight.Bold))
        game.setStyleSheet(f"color:{TEXT};")

        rs = bet.get("risk_score", "—")
        rc = GREEN if isinstance(rs,int) and rs<=30 else AMBER if isinstance(rs,int) and rs<=60 else RED
        risk = QLabel(f"Risk {rs}/100")
        risk.setFont(QFont(MONO, 9))
        risk.setStyleSheet(f"color:{rc};")

        top.addWidget(game)
        top.addStretch()
        top.addWidget(risk)
        info.addLayout(top)

        bet_lbl = QLabel(self._bet_line())
        bet_lbl.setFont(QFont(MONO, 11))
        bet_lbl.setStyleSheet(f"color:{color};")
        info.addWidget(bet_lbl)

        detail = QLabel(self._detail_line())
        detail.setFont(QFont(MONO, 10))
        detail.setStyleSheet(f"color:{MUTED};")
        info.addWidget(detail)

        lay.addLayout(info, stretch=1)

        # Action column
        right = QVBoxLayout()
        right.setSpacing(6)
        right.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._stake_lbl = QLabel(f"€{self._stake:.2f}")
        self._stake_lbl.setFont(QFont(MONO, 14, QFont.Weight.Bold))
        self._stake_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stake_lbl.setStyleSheet(f"color:{GREEN if self._placed else TEXT_D};")

        self._btn = QPushButton()
        self._btn.setMinimumWidth(100)
        self._btn.clicked.connect(self._toggle)
        self._refresh_btn()

        right.addWidget(self._stake_lbl)
        right.addWidget(self._btn)
        lay.addLayout(right)

    def _bet_line(self):
        b = self.bet
        odds = b.get('best_odds','')
        book = b.get('bookmaker','')
        lbl  = b.get('bet_label', b.get('player',''))
        return f"{lbl}  @  {odds}  [{book}]"

    def _detail_line(self):
        b   = self.bet
        cat = self.cat
        if cat == "h2h":
            return (f"Model {b.get('model_prob',0)*100:.1f}%  ·  "
                    f"Implied {b.get('implied_prob',0)*100:.1f}% [{b.get('ref_source','consensus')}]  ·  "
                    f"Edge {b.get('edge',0)*100:+.1f}%  ·  {b.get('confidence','').upper()}")
        elif cat in ("spread","totals"):
            return (f"Disagreement {abs(b.get('edge',0)):.1f} pts  ·  "
                    f"{b.get('confidence','').upper()}  ·  Kelly €{b.get('simulated_stake',0):.2f}")
        else:
            return (f"Avg {b.get('avg',0):.1f}  ·  "
                    f"Edge {b.get('edge_pts', b.get('edge',0)):+.1f}  ·  "
                    f"σ={b.get('std',0):.1f}  ·  {b.get('confidence','LOW').upper()}")

    def _refresh_btn(self):
        if self._placed:
            self._btn.setText("✓  PLACED")
            self._btn.setObjectName("placed")
        else:
            self._btn.setText("PLACE BET")
            self._btn.setObjectName("unplaced")
        self._btn.style().unpolish(self._btn)
        self._btn.style().polish(self._btn)
        self._stake_lbl.setStyleSheet(f"color:{GREEN if self._placed else TEXT_D};")

    def _toggle(self):
        if self._placed:
            self._placed = False
            self._stake  = self.bet.get("simulated_stake", 10.0)
            self._stake_lbl.setText(f"€{self._stake:.2f}")
            self._refresh_btn()
            self.placed_changed.emit(self.bet_id, False, 0.0)
        else:
            dlg = StakeDialog(
                self.bet.get("bet_label", self.bet.get("player", "")),
                self.bet.get("simulated_stake", 10.0), self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self._stake  = dlg.stake
                self._placed = True
                self._stake_lbl.setText(f"€{self._stake:.2f}")
                self._refresh_btn()
                self.placed_changed.emit(self.bet_id, True, self._stake)


# ── Bets panel ────────────────────────────────────────────────────────────────
class BetsPanel(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._inner = QWidget()
        self._lay   = QVBoxLayout(self._inner)
        self._lay.setSpacing(6)
        self._lay.setContentsMargins(0, 0, 8, 0)
        self._lay.addStretch()
        self.setWidget(self._inner)
        self._cards: dict[str, BetCard] = {}
        self._path: Path | None = None

    def load(self, path: Path):
        self._path = path
        for i in reversed(range(self._lay.count())):
            w = self._lay.itemAt(i)
            if w and w.widget(): w.widget().deleteLater()
        self._cards.clear()

        try:
            with open(path) as f: picks = json.load(f)
        except Exception: return

        vbs   = picks.get("value_bets", [])
        props = picks.get("prop_bets", [])
        if not vbs and not props:
            lbl = QLabel("No value bets found for today.")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"color:{MUTED}; font-size:13px; padding:40px;")
            self._lay.insertWidget(0, lbl)
            return

        groups = {"h2h": [], "spread": [], "totals": [], "props": []}
        for i, vb in enumerate(vbs):   groups[vb.get("market","h2h")].append((f"vb_{i}",   vb))
        for i, pb in enumerate(props): groups["props"].append((f"prop_{i}", pb))

        pos = 0
        for cat, bets in groups.items():
            if not bets: continue
            color, label = CATEGORY[cat]
            n_placed = sum(1 for _, b in bets if b.get("placed"))

            hdr_w = QWidget()
            hdr_l = QHBoxLayout(hdr_w)
            hdr_l.setContentsMargins(4, 10, 4, 4)
            cat_lbl = QLabel(f"── {label}")
            cat_lbl.setFont(QFont(MONO, 9, QFont.Weight.Bold))
            cat_lbl.setStyleSheet(f"color:{color}; letter-spacing:2px;")
            cnt_lbl = QLabel(f"{len(bets)} suggested  ·  {n_placed} placed")
            cnt_lbl.setFont(QFont(MONO, 9))
            cnt_lbl.setStyleSheet(f"color:{MUTED};")
            hdr_l.addWidget(cat_lbl)
            hdr_l.addStretch()
            hdr_l.addWidget(cnt_lbl)
            self._lay.insertWidget(pos, hdr_w); pos += 1

            for bet_id, bet in bets:
                card = BetCard(bet, bet_id, cat)
                card.placed_changed.connect(self._save)
                self._lay.insertWidget(pos, card)
                self._cards[bet_id] = card
                pos += 1

        self._lay.addStretch()

    def _save(self, bet_id: str, placed: bool, stake: float):
        if not self._path or not self._path.exists(): return
        try:
            with open(self._path) as f: picks = json.load(f)
            if bet_id.startswith("vb_"):
                idx = int(bet_id[3:])
                b   = picks["value_bets"][idx]
            else:
                idx = int(bet_id[5:])
                b   = picks["prop_bets"][idx]
            b["placed"]        = placed
            b["placed_stake"]  = stake if placed else None
            b["placed_return"] = round(stake * b.get("best_odds", 1), 2) if placed else None
            b["placed_profit"] = round(stake * b.get("best_odds", 1) - stake, 2) if placed else None
            with open(self._path, "w") as f: json.dump(picks, f, indent=2)
        except Exception as e: print(f"[app] save error: {e}")


# ── Main window ───────────────────────────────────────────────────────────────
class App(QMainWindow):
    APP_DIR  = Path(__file__).parent
    DATA_DIR = APP_DIR / "data"

    def __init__(self):
        super().__init__()
        self.worker = None
        self.setWindowTitle("NBA BETTING ASSISTANT")
        self.setMinimumSize(1280, 800)
        self.resize(1500, 900)
        self._build()
        self._refresh_stats()
        self._load_todays_bets()
        t = QTimer(self); t.timeout.connect(self._tick); t.start(1000); self._tick()

    # ── Layout ────────────────────────────────────────────────────────────────
    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(28, 20, 28, 14)
        lay.setSpacing(14)
        lay.addLayout(self._mk_header())
        lay.addWidget(self._divider())
        lay.addWidget(self._mk_tabs(), stretch=1)
        lay.addLayout(self._mk_statusbar())

    def _mk_header(self):
        h = QHBoxLayout()

        left = QVBoxLayout()
        left.setSpacing(5)

        title = QLabel("NBA BETTING ASSISTANT")
        title.setFont(QFont(MONO, 24, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{CYAN}; letter-spacing:5px;")

        sub = QLabel("PINNACLE REFERENCE  ·  QUARTER-KELLY SIZING  ·  PLACED BET TRACKING")
        sub.setFont(QFont(MONO, 10))
        sub.setStyleSheet(f"color:{MUTED}; letter-spacing:3px;")

        left.addWidget(title)
        left.addWidget(sub)

        self._clock = QLabel()
        self._clock.setFont(QFont(MONO, 11))
        self._clock.setStyleSheet(f"color:{TEXT_D};")
        self._clock.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        h.addLayout(left)
        h.addStretch()
        h.addWidget(self._clock)
        return h

    def _divider(self):
        f = QFrame(); f.setFixedHeight(1)
        f.setStyleSheet(f"background:{BORDER};")
        return f

    def _mk_tabs(self):
        self._tabs = QTabWidget()
        self._tabs.addTab(self._tab_picks(),     "TODAY'S BETS")
        self._tabs.addTab(self._tab_results(),   "RESULTS")
        self._tabs.addTab(self._tab_dashboard(), "DASHBOARD")
        self._tabs.addTab(self._tab_console(),   "CONSOLE")
        return self._tabs

    # ── TODAY'S BETS ──────────────────────────────────────────────────────────
    def _tab_picks(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 14, 0, 0)
        lay.setSpacing(12)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self._btn_picks   = QPushButton("⚡  GET TODAY'S PICKS")
        self._btn_settle  = QPushButton("◈  SETTLE YESTERDAY")
        self._btn_morning = QPushButton("▶  FULL MORNING RUN")
        self._btn_settle.setObjectName("amber")
        for btn in [self._btn_picks, self._btn_settle, self._btn_morning]:
            btn.setMinimumHeight(44)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        self._btn_picks.clicked.connect(self._run_picks)
        self._btn_settle.clicked.connect(self._run_settle)
        self._btn_morning.clicked.connect(self._run_morning)
        lay.addLayout(btn_row)

        # Progress area — shown while picks are running
        self._progress_frame = QFrame()
        self._progress_frame.setObjectName("card")
        self._progress_frame.setVisible(False)
        pf_lay = QVBoxLayout(self._progress_frame)
        pf_lay.setContentsMargins(20, 16, 20, 16)
        pf_lay.setSpacing(10)
        self._progress_status = QLabel("Fetching today's picks…")
        self._progress_status.setFont(QFont(MONO, 12))
        self._progress_status.setStyleSheet(f"color:{CYAN};")
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setFixedHeight(6)
        self._progress_bar.setTextVisible(False)
        self._progress_hint = QLabel("This takes 2–3 minutes while injury data is fetched.")
        self._progress_hint.setFont(QFont(MONO, 10))
        self._progress_hint.setStyleSheet(f"color:{MUTED};")
        pf_lay.addWidget(self._progress_status)
        pf_lay.addWidget(self._progress_bar)
        pf_lay.addWidget(self._progress_hint)
        lay.addWidget(self._progress_frame)

        # Stat cards
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        self._c_games      = StatCard("Games")
        self._c_h2h        = StatCard("H2H Acc")
        self._c_vb         = StatCard("VB Record")
        self._c_sug_roi    = StatCard("Suggested ROI", color=MUTED)
        self._c_sug_pnl    = StatCard("Suggested P&L", color=MUTED)
        self._c_placed_roi = StatCard("Placed ROI",    color=GREEN)
        self._c_placed_pnl = StatCard("Placed P&L",    color=GREEN)
        for c in [self._c_games, self._c_h2h, self._c_vb,
                  self._c_sug_roi, self._c_sug_pnl,
                  self._c_placed_roi, self._c_placed_pnl]:
            cards_row.addWidget(c)
        lay.addLayout(cards_row)

        # Bet cards
        bets_hdr = QLabel("TODAY'S SUGGESTED BETS — click PLACE BET to register a bet")
        bets_hdr.setFont(QFont(MONO, 9))
        bets_hdr.setStyleSheet(f"color:{MUTED}; letter-spacing:2px;")
        lay.addWidget(bets_hdr)

        self._bets_panel = BetsPanel()
        lay.addWidget(self._bets_panel, stretch=1)
        return w

    # ── RESULTS ───────────────────────────────────────────────────────────────
    def _tab_results(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 14, 0, 0)
        lay.setSpacing(12)

        btn_row = QHBoxLayout()
        self._btn_results = QPushButton("◈  LOAD YESTERDAY'S RESULTS")
        self._btn_results.setObjectName("amber")
        self._btn_results.setMinimumHeight(44)
        self._btn_results.clicked.connect(self._show_yesterday)
        btn_row.addWidget(self._btn_results)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        # Results table + console split
        splitter = QSplitter(Qt.Orientation.Vertical)

        self._results_table = QTableWidget()
        self._results_table.setColumnCount(6)
        self._results_table.setHorizontalHeaderLabels(
            ["Date", "Market", "Bet", "Outcome", "Suggested P&L", "Placed P&L"])
        self._results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for col in [0,1,3,4,5]:
            self._results_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents)
        self._results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._results_table.verticalHeader().setVisible(False)
        self._results_table.setShowGrid(False)
        splitter.addWidget(self._results_table)

        self._results_console = Console()
        splitter.addWidget(self._results_console)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        lay.addWidget(splitter, stretch=1)
        return w

    # ── DASHBOARD ─────────────────────────────────────────────────────────────
    def _tab_dashboard(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 14, 0, 0)
        lay.setSpacing(10)

        btn_row = QHBoxLayout()
        self._btn_refresh_dash = QPushButton("↻  REFRESH DASHBOARD")
        self._btn_refresh_dash.setObjectName("green")
        self._btn_refresh_dash.setMinimumHeight(44)
        self._btn_refresh_dash.clicked.connect(self._refresh_dashboard)
        btn_row.addWidget(self._btn_refresh_dash)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        if HAS_WEBENGINE:
            self._webview = QWebEngineView()
            self._webview.setStyleSheet(f"background:{BG};")
            lay.addWidget(self._webview, stretch=1)
        else:
            no_web = QLabel(
                "PyQt6-WebEngine not installed.\n\n"
                "Run:  pip install PyQt6-WebEngine\n\n"
                "Then restart the app."
            )
            no_web.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_web.setFont(QFont(MONO, 12))
            no_web.setStyleSheet(f"color:{MUTED};")
            lay.addWidget(no_web, stretch=1)
        return w

    # ── CONSOLE ───────────────────────────────────────────────────────────────
    def _tab_console(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 14, 0, 0)
        lay.setSpacing(8)
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

    def _mk_statusbar(self):
        h = QHBoxLayout()
        self._status = QLabel("READY")
        self._status.setFont(QFont(MONO, 10))
        self._status.setStyleSheet(f"color:{MUTED};")
        self._status_bar = QProgressBar()
        self._status_bar.setRange(0, 0)
        self._status_bar.setVisible(False)
        self._status_bar.setMaximumWidth(140)
        self._status_bar.setFixedHeight(3)
        self._status_bar.setTextVisible(False)
        h.addWidget(self._status)
        h.addStretch()
        h.addWidget(self._status_bar)
        return h

    # ── Actions ───────────────────────────────────────────────────────────────
    def _run_picks(self):
        self._show_progress(True, "Fetching today's picks…",
                            "This takes 2–3 minutes while injury data is fetched.")
        self._launch("dry_run.py", "Fetching today's picks…", self._on_picks_done)

    def _run_settle(self):
        self._launch("results_tracker.py", "Settling yesterday…", self._on_settle_done)

    def _run_morning(self):
        self._launch("results_tracker.py", "Morning: settling…",
            lambda ok: ok and self._launch("report.py", "Generating report…",
            lambda ok2: ok2 and self._refresh_dashboard_bg(
            lambda: self._launch("dry_run.py", "Fetching picks…", self._on_picks_done))))

    def _refresh_dashboard(self):
        self._launch("dashboard.py", "Generating dashboard…", self._on_dash_done)

    def _refresh_dashboard_bg(self, then=None):
        """Regenerates dashboard silently then calls then()."""
        self._launch("dashboard.py", "Updating dashboard…",
                     lambda ok: ok and then and then())

    def _on_picks_done(self, ok):
        self._show_progress(False)
        if ok:
            self._refresh_stats()
            self._load_todays_bets()
            self._tabs.setCurrentIndex(0)

    def _on_settle_done(self, ok):
        if ok:
            self._refresh_stats()
            self._show_yesterday()

    def _on_dash_done(self, ok):
        if ok: self._load_dashboard()

    def _load_dashboard(self):
        path = self.APP_DIR / "dashboard.html"
        if HAS_WEBENGINE and path.exists():
            self._webview.load(QUrl.fromLocalFile(str(path.resolve())))

    def _load_todays_bets(self):
        date = (datetime.now(timezone.utc) + ET).date().isoformat()
        path = self.DATA_DIR / f"picks_{date}.json"
        if path.exists(): self._bets_panel.load(path)

    def _show_progress(self, visible: bool, status: str = "", hint: str = ""):
        self._progress_frame.setVisible(visible)
        if visible:
            self._progress_status.setText(status)
            self._progress_hint.setText(hint)

    def _show_yesterday(self):
        yesterday = (datetime.now(timezone.utc) + ET - timedelta(days=1)).date().isoformat()
        path = self.DATA_DIR / f"picks_{yesterday}.json"

        self._results_table.setRowCount(0)
        self._results_console.clear()

        if not path.exists():
            self._results_console.append_text(
                f"No picks file for {yesterday}.\nRun 'Settle Yesterday' first.\n")
            self._tabs.setCurrentIndex(1)
            return

        with open(path) as f: picks = json.load(f)
        s = picks.get("results_summary")
        if not s:
            self._results_console.append_text(
                f"Results for {yesterday} not yet settled.\n")
            self._tabs.setCurrentIndex(1)
            return

        n = s.get("settled", 0)
        # Summary in console
        c = self._results_console
        c.append_text(f"{'='*60}\n  RESULTS — {yesterday}  ({n} games settled)\n{'='*60}\n\n")
        if n:
            c.append_text(f"  Market accuracy:\n")
            c.append_text(f"    H2H:    {s['h2h_correct']}/{n}  ({s['h2h_correct']/n*100:.1f}%)\n")
            c.append_text(f"    Spread: {s['spread_correct']}/{n}  ({s['spread_correct']/n*100:.1f}%)\n")
            c.append_text(f"    Totals: {s['total_correct']}/{n}  ({s['total_correct']/n*100:.1f}%)\n\n")

        for track, label in [("suggested","SUGGESTED (Kelly)"), ("placed","PLACED (actual)")]:
            staked = s.get(f"{track}_staked", 0.0)
            pnl    = s.get(f"{track}_pnl", 0.0)
            won    = s.get(f"{track}_won", 0)
            total  = s.get(f"{track}_total", 0)
            if staked == 0 and track == "placed": continue
            roi = pnl / staked * 100 if staked else 0
            c.append_text(f"  {label}:\n")
            c.append_text(f"    {won}/{total}  ·  €{staked:.2f} staked  ·  "
                         f"€{pnl:+.2f} P&L  ·  {roi:+.1f}% ROI\n\n")

        # Fill table
        all_bets = picks.get("value_bets", []) + picks.get("prop_bets", [])
        settled  = [b for b in all_bets if b.get("outcome")]
        self._results_table.setRowCount(len(settled))
        for row, b in enumerate(settled):
            won     = b.get("outcome") == "won"
            placed  = b.get("placed", False)
            col     = GREEN if won else RED
            sug_pnl = b.get("actual_pnl", 0.0) or 0.0
            pl_pnl  = b.get("placed_actual_pnl")
            pl_str  = f"€{pl_pnl:+.2f}" if pl_pnl is not None else "—"
            vals    = [
                yesterday,
                b.get("market", "props").upper(),
                b.get("bet_label", b.get("player", "")),
                ("✓" if won else "✗") + (" ●" if placed else " ○"),
                f"€{sug_pnl:+.2f}",
                pl_str,
            ]
            for col_idx, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter |
                    (Qt.AlignmentFlag.AlignRight if col_idx in (4,5)
                     else Qt.AlignmentFlag.AlignLeft))
                if col_idx in (3,4,5): item.setForeground(QColor(col))
                self._results_table.setItem(row, col_idx, item)

        c.append_text("  ● = placed   ○ = not placed\n")
        self._tabs.setCurrentIndex(1)

    # ── Worker plumbing ───────────────────────────────────────────────────────
    def _launch(self, script, label, on_done=None, args=None):
        if self.worker and self.worker.isRunning(): return
        self._set_btns(False)
        self._set_status(label.upper(), MUTED)
        self._status_bar.setVisible(True)
        self.console.append_text(f"\n── {label} ──\n")

        self.worker = Worker(script, args)
        self.worker.output.connect(self.console.append_text)
        self.worker.done_ok.connect(lambda: self._on_done(True, on_done))
        self.worker.done_err.connect(lambda _: self._on_done(False, on_done))
        self.worker.start()

    def _on_done(self, ok, callback):
        self._set_btns(True)
        self._status_bar.setVisible(False)
        self._show_progress(False)
        if ok:
            self._set_status("DONE", GREEN)
            self.console.append_text("\n── Completed successfully ──\n")
        else:
            self._set_status("ERROR", RED)
            self.console.append_text("\n── Completed with errors ──\n")
        QTimer.singleShot(3000, lambda: self._set_status("READY", MUTED))
        if callback: callback(ok)

    def _set_btns(self, enabled):
        for b in [self._btn_picks, self._btn_settle, self._btn_morning,
                  self._btn_results, self._btn_refresh_dash]:
            b.setEnabled(enabled)

    def _set_status(self, text, color):
        self._status.setText(text)
        self._status.setStyleSheet(f"color:{color};")

    # ── Stats refresh ─────────────────────────────────────────────────────────
    def _refresh_stats(self):
        files = sorted(self.DATA_DIR.glob("picks_*.json"))
        settled = []
        for f in files:
            try:
                with open(f) as fp: p = json.load(fp)
                if p.get("results_summary"): settled.append(p)
            except Exception: pass
        if not settled: return

        games=h2h_c=h2h_t=vb_won=vb_total=0
        sug_staked=sug_pnl=pl_staked=pl_pnl=0.0

        all_vb = []
        for p in settled:
            s = p["results_summary"]
            n = s.get("settled", 0)
            games    += n
            h2h_c    += s.get("h2h_correct", 0)
            h2h_t    += n
            vb_won   += s.get("suggested_won",   s.get("value_bets_won", 0))
            vb_total += s.get("suggested_total", s.get("value_bets_total", 0))
            sug_staked += s.get("suggested_staked", s.get("value_bets_staked", 0.0))
            sug_pnl    += s.get("suggested_pnl",    s.get("value_bets_pnl", 0.0))
            pl_staked  += s.get("placed_staked", 0.0)
            pl_pnl     += s.get("placed_pnl", 0.0)
            for vb in p.get("value_bets", []):
                if vb.get("outcome"): all_vb.append({**vb, "date": p["date"]})

        def pct(w,t): return f"{w/t*100:.1f}%" if t else "—"
        def roi(p,s): return f"{p/s*100:+.1f}%" if s else "—"

        self._c_games.set(str(games))
        self._c_h2h.set(pct(h2h_c, h2h_t))
        self._c_vb.set(f"{vb_won}/{vb_total}")
        self._c_sug_roi.set(roi(sug_pnl, sug_staked), MUTED)
        self._c_sug_pnl.set(f"€{sug_pnl:+.0f}", GREEN if sug_pnl >= 0 else RED)
        self._c_placed_roi.set(roi(pl_pnl, pl_staked) if pl_staked else "—", GREEN)
        self._c_placed_pnl.set(f"€{pl_pnl:+.0f}" if pl_staked else "—",
                               GREEN if pl_pnl >= 0 else RED)

    # ── Dashboard ─────────────────────────────────────────────────────────────
    def _refresh_dashboard(self):
        self._launch("dashboard.py", "Generating dashboard…", self._on_dash_done)

    # ── Clock ─────────────────────────────────────────────────────────────────
    def _tick(self):
        now = datetime.now()
        et  = datetime.now(timezone.utc) + ET
        self._clock.setText(
            f"{now.strftime('%a %d %b %Y  %H:%M:%S')}  ·  ET {et.strftime('%H:%M')}")


# ── Entry ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    os.environ.setdefault("QT_LOGGING_RULES", "*.warning=false")
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    app.setStyle("Fusion")
    window = App()
    window.show()
    sys.exit(app.exec())
