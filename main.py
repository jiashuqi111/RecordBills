import os, sys, sqlite3, csv, shutil, calendar
from pathlib import Path
from datetime import date, datetime, timedelta

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QDialog, QFormLayout,
    QLineEdit, QComboBox, QDateEdit, QMessageBox, QFileDialog,
    QListWidget, QAbstractItemView, QInputDialog, QGroupBox
)
from PySide6.QtCore import Qt, QDate

# ---- Matplotlib（嵌入到 Qt） ----
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
# ==== Matplotlib 中文显示设置（全局） ====
from matplotlib import rcParams
rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC', 'Noto Sans SC', 'Arial Unicode MS']
rcParams['axes.unicode_minus'] = False  # 解决坐标轴负号显示为方块的问题
# ========================
# 数据库位置（当前目录 one_account.db）
# 如需放到 %APPDATA%\OneAccountPC\ 下，可按之前说法替换为固定路径逻辑
# ========================
DB_PATH = "one_account.db"

DDL = """
CREATE TABLE IF NOT EXISTS transactions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,          -- 'income' or 'expense'
  category TEXT NOT NULL,
  amount REAL NOT NULL,
  date TEXT NOT NULL,          -- 'YYYY-MM-DD'
  note TEXT
);
CREATE INDEX IF NOT EXISTS idx_t_date ON transactions(date);

-- 分类表（用于下拉选项）
CREATE TABLE IF NOT EXISTS categories(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);
"""

DEFAULT_CATEGORIES = ["餐饮","交通","购物","居住","工资","其他"]

# ---------- DB helpers ----------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as c:
        for stmt in DDL.strip().split(";"):
            s = stmt.strip()
            if s:
                c.execute(s)
        # 默认分类
        n = c.execute("SELECT COUNT(*) AS n FROM categories").fetchone()["n"]
        if n == 0:
            c.executemany("INSERT INTO categories(name) VALUES(?)",
                          [(x,) for x in DEFAULT_CATEGORIES])
        c.commit()

# ---------- 分类 CRUD ----------
def list_categories():
    with get_conn() as c:
        rows = c.execute("SELECT name FROM categories ORDER BY id").fetchall()
        return [r["name"] for r in rows]

def add_category(name: str):
    name = (name or "").strip()
    if not name:
        raise ValueError("分类名不能为空")
    with get_conn() as c:
        try:
            c.execute("INSERT INTO categories(name) VALUES(?)", (name,))
            c.commit()
        except sqlite3.IntegrityError:
            raise ValueError("该分类已存在")

def delete_category(name: str):
    name = (name or "").strip()
    if not name:
        return
    with get_conn() as c:
        c.execute("DELETE FROM categories WHERE name=?", (name,))
        c.commit()

# ---------- 交易 CRUD & 统计 ----------
def insert_txn(t):
    with get_conn() as c:
        c.execute("""INSERT INTO transactions(type,category,amount,date,note)
                     VALUES(?,?,?,?,?)""",
                  (t["type"], t["category"], t["amount"], t["date"], t.get("note")))
        c.commit()

def update_txn(tid, t):
    with get_conn() as c:
        c.execute("""UPDATE transactions
                     SET type=?, category=?, amount=?, date=?, note=?
                     WHERE id=?""",
                  (t["type"], t["category"], t["amount"], t["date"], t.get("note"), tid))
        c.commit()

def delete_txn(tid):
    with get_conn() as c:
        c.execute("DELETE FROM transactions WHERE id=?", (tid,))
        c.commit()

def list_txns(month=None):
    with get_conn() as c:
        if month:  # 'YYYY-MM'
            start = f"{month}-01"
            y, m = map(int, month.split("-"))
            end = f"{y+1}-01-01" if m == 12 else f"{y}-{m+1:02d}-01"
            rows = c.execute("""SELECT * FROM transactions
                                WHERE date>=? AND date<? 
                                ORDER BY date DESC, id DESC""", (start, end)).fetchall()
        else:
            rows = c.execute("""SELECT * FROM transactions 
                                ORDER BY date DESC, id DESC""").fetchall()
        return rows

def sum_month(month):
    with get_conn() as c:
        start = f"{month}-01"
        y, m = map(int, month.split("-"))
        end = f"{y+1}-01-01" if m == 12 else f"{y}-{m+1:02d}-01"
        rows = c.execute("""SELECT type, SUM(amount) total 
                            FROM transactions 
                            WHERE date>=? AND date<? 
                            GROUP BY type""", (start, end)).fetchall()
        r = {"income": 0.0, "expense": 0.0}
        for row in rows:
            r[row["type"]] = float(row["total"] or 0)
        r["balance"] = r["income"] - r["expense"]
        return r

def sum_today_by_type(tp: str) -> float:
    today = date.today().strftime("%Y-%m-%d")
    with get_conn() as c:
        row = c.execute("""
            SELECT SUM(amount) AS total
            FROM transactions
            WHERE type=? AND date=?
        """, (tp, today)).fetchone()
        return float(row["total"] or 0.0)

def week_range_of(d: date):
    # 返回本周的周一和周日（含）  (ISO: Mon=0 .. Sun=6)
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday

def sum_week_expense(d: date) -> float:
    monday, sunday = week_range_of(d)
    with get_conn() as c:
        row = c.execute("""
            SELECT SUM(amount) AS total
            FROM transactions
            WHERE type='expense' AND date BETWEEN ? AND ?
        """, (monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d"))).fetchone()
        return float(row["total"] or 0.0)

def daily_expense_for_month(year_month: str):
    # 返回 (days[1..last], totals[对应每日消费额])
    y, m = map(int, year_month.split("-"))
    last_day = calendar.monthrange(y, m)[1]
    start = f"{year_month}-01"
    end = f"{y+1}-01-01" if m == 12 else f"{y}-{m+1:02d}-01"
    with get_conn() as c:
        rows = c.execute("""
            SELECT date, SUM(amount) AS total
            FROM transactions
            WHERE type='expense' AND date>=? AND date<?
            GROUP BY date
            ORDER BY date
        """, (start, end)).fetchall()
    # 填充所有天
    totals_by_day = {int(r["date"].split("-")[2]): float(r["total"] or 0) for r in rows}
    days = list(range(1, last_day+1))
    totals = [totals_by_day.get(d, 0.0) for d in days]
    return days, totals

def category_expense_share_for_month(year_month: str):
    y, m = map(int, year_month.split("-"))
    start = f"{year_month}-01"
    end = f"{y+1}-01-01" if m == 12 else f"{y}-{m+1:02d}-01"
    with get_conn() as c:
        rows = c.execute("""
            SELECT category, SUM(amount) AS total
            FROM transactions
            WHERE type='expense' AND date>=? AND date<?
            GROUP BY category
            HAVING total>0
            ORDER BY total DESC
        """, (start, end)).fetchall()
    labels = [r["category"] for r in rows]
    values = [float(r["total"] or 0) for r in rows]
    return labels, values

# ---------- UI：分类管理对话框 ----------
class CategoryManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("分类管理")
        self.resize(360, 420)

        v = QVBoxLayout(self)
        self.listw = QListWidget()
        self.listw.setSelectionMode(QAbstractItemView.SingleSelection)
        v.addWidget(self.listw)

        btns = QHBoxLayout()
        self.btn_add = QPushButton("新增分类")
        self.btn_del = QPushButton("删除选中")
        btns.addWidget(self.btn_add)
        btns.addWidget(self.btn_del)
        v.addLayout(btns)

        self.btn_add.clicked.connect(self.on_add)
        self.btn_del.clicked.connect(self.on_del)

        self.reload()

    def reload(self):
        self.listw.clear()
        for name in list_categories():
            self.listw.addItem(name)

    def on_add(self):
        name, ok = QInputDialog.getText(self, "新增分类", "分类名称：")
        if not ok:
            return
        try:
            add_category(name)
            self.reload()
        except ValueError as e:
            QMessageBox.warning(self, "提示", str(e))

    def on_del(self):
        it = self.listw.currentItem()
        if not it:
            QMessageBox.information(self, "提示", "请先选中一条分类")
            return
        name = it.text()
        if QMessageBox.question(self, "确认", f"确定删除分类「{name}」？（不会影响历史账单）") == QMessageBox.Yes:
            delete_category(name)
            self.reload()

# ---------- UI：新增/编辑 交易 ----------
class TxnDialog(QDialog):
    def __init__(self, parent=None, data=None):
        super().__init__(parent)
        self.setWindowTitle("记一笔" if data is None else "编辑")
        self.data = data or {}
        form = QFormLayout(self)

        self.type_cb = QComboBox()
        self.type_cb.addItems(["expense","income"])
        if data: self.type_cb.setCurrentText(data["type"])
        form.addRow("类型", self.type_cb)

        self.cat_cb = QComboBox()
        cats = list_categories()
        self.cat_cb.addItems(cats)
        if data and data["category"] not in cats:
            self.cat_cb.insertItem(0, data["category"])
        if data:
            self.cat_cb.setCurrentText(data["category"])
        form.addRow("分类", self.cat_cb)

        self.amount_le = QLineEdit()
        if data: self.amount_le.setText(str(data["amount"]))
        form.addRow("金额", self.amount_le)

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        d = date.today() if not data else datetime.strptime(data["date"], "%Y-%m-%d").date()
        self.date_edit.setDate(QDate(d.year, d.month, d.day))
        form.addRow("日期", self.date_edit)

        self.note_le = QLineEdit()
        if data: self.note_le.setText(data.get("note") or "")
        form.addRow("备注", self.note_le)

        btns = QHBoxLayout()
        ok = QPushButton("保存"); cancel = QPushButton("取消")
        btns.addWidget(ok); btns.addWidget(cancel)
        form.addRow(btns)

        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

    def get_value(self):
        try:
            amt = float(self.amount_le.text())
            if amt <= 0: raise ValueError()
        except Exception:
            QMessageBox.warning(self, "提示", "请输入有效金额")
            return None
        d = self.date_edit.date()
        return {
            "type": self.type_cb.currentText(),
            "category": self.cat_cb.currentText(),
            "amount": amt,
            "date": f"{d.year():04d}-{d.month():02d}-{d.day():02d}",
            "note": self.note_le.text().strip() or None
        }

# ---------- UI：主窗口 ----------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OneAccount (PC)")
        self.resize(980, 740)

        central = QWidget(); self.setCentralWidget(central)
        v = QVBoxLayout(central)

        # 顶部：月份选择 + “今日/本周/本月”汇总 + 分类管理
        top = QHBoxLayout()
        self.month_edit = QDateEdit()
        self.month_edit.setDisplayFormat("yyyy-MM")
        self.month_edit.setDate(QDate.currentDate())
        self.month_edit.setCalendarPopup(True)
        top.addWidget(QLabel("月份："))
        top.addWidget(self.month_edit)

        # 今日消费/收入 + 本月汇总 + 本周消费
        self.today_cost_label = QLabel("今日消费 0.00")
        self.today_cost_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.today_income_label = QLabel("今日收入 0.00")
        self.today_income_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.week_label = QLabel("本周消费 0.00")
        self.week_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.sum_label = QLabel("本月 收入 0.00 | 支出 0.00 | 结余 0.00")
        self.sum_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        # 分类管理按钮
        self.btn_cat_mgr = QPushButton("分类管理")

        top.addStretch(1)
        top.addWidget(self.today_cost_label)
        top.addSpacing(12)
        top.addWidget(self.today_income_label)
        top.addSpacing(12)
        top.addWidget(self.week_label)
        top.addSpacing(12)
        top.addWidget(self.sum_label)
        top.addSpacing(12)
        top.addWidget(self.btn_cat_mgr)
        v.addLayout(top)

        # 表格
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["ID","类型","分类","金额","日期","备注"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnHidden(0, True)  # 隐藏ID列
        v.addWidget(self.table)

        # 底部按钮
        bottom = QHBoxLayout()
        self.btn_add = QPushButton("新增")
        self.btn_edit = QPushButton("编辑")
        self.btn_del = QPushButton("删除")
        self.btn_export = QPushButton("导出CSV")
        bottom.addWidget(self.btn_add)
        bottom.addWidget(self.btn_edit)
        bottom.addWidget(self.btn_del)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_export)
        v.addLayout(bottom)

        # ===== 统计图表区域 =====
        charts_box = QHBoxLayout()
        # 左：月度每日消费趋势
        self.fig_line = Figure(figsize=(5, 3))
        self.canvas_line = FigureCanvas(self.fig_line)
        gb1 = QGroupBox("月度每日消费趋势（本月）")
        gb1_layout = QVBoxLayout(gb1)
        gb1_layout.addWidget(self.canvas_line)

        # 右：本月分类占比
        self.fig_pie = Figure(figsize=(5, 3))
        self.canvas_pie = FigureCanvas(self.fig_pie)
        gb2 = QGroupBox("分类占比（本月消费）")
        gb2_layout = QVBoxLayout(gb2)
        gb2_layout.addWidget(self.canvas_pie)

        charts_box.addWidget(gb1, 1)
        charts_box.addWidget(gb2, 1)
        v.addLayout(charts_box)

        # 信号
        self.month_edit.dateChanged.connect(self.refresh)
        self.btn_add.clicked.connect(self.on_add)
        self.btn_edit.clicked.connect(self.on_edit)
        self.btn_del.clicked.connect(self.on_del)
        self.btn_export.clicked.connect(self.on_export)
        self.btn_cat_mgr.clicked.connect(self.on_cat_mgr)

        self.refresh()

    def current_month_str(self):
        d = self.month_edit.date()
        return f"{d.year():04d}-{d.month():02d}"

    def refresh(self):
        # 表格
        month = self.current_month_str()
        rows = list_txns(month)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(str(r["id"])))
            self.table.setItem(i, 1, QTableWidgetItem(r["type"]))
            self.table.setItem(i, 2, QTableWidgetItem(r["category"]))
            self.table.setItem(i, 3, QTableWidgetItem(f'{float(r["amount"]):.2f}'))
            self.table.setItem(i, 4, QTableWidgetItem(r["date"]))
            self.table.setItem(i, 5, QTableWidgetItem(r["note"] or ""))
        self.table.resizeColumnsToContents()

        # 顶部统计：今日/本周/本月
        today_cost = sum_today_by_type("expense")
        today_income = sum_today_by_type("income")
        week_cost = sum_week_expense(date.today())
        s = sum_month(month)

        # 今日消费颜色：>0 红色，=0 灰色
        self.today_cost_label.setText(f"今日消费 {today_cost:.2f}")
        self.today_cost_label.setStyleSheet("color:#d32f2f;" if today_cost > 0 else "color:#888888;")
        self.today_income_label.setText(f"今日收入 {today_income:.2f}")
        self.week_label.setText(f"本周消费 {week_cost:.2f}")
        self.sum_label.setText(f'本月 收入 {s["income"]:.2f} | 支出 {s["expense"]:.2f} | 结余 {s["balance"]:.2f}')

        # 图表
        self.update_charts(month)

    def update_charts(self, month: str):
        # 折线图：本月每日消费
        days, totals = daily_expense_for_month(month)
        self.fig_line.clear()
        self.fig_line.subplots_adjust(bottom=0.18)
        self.fig_line.subplots_adjust(left=0.15)
        ax = self.fig_line.add_subplot(111)
        ax.plot(days, totals, marker='o')
        ax.set_xlabel("日期（日）")
        ax.set_ylabel("消费金额")
        ax.set_title(f"{month} 每日消费趋势")
        ax.grid(True, linestyle="--", alpha=0.5)
        self.canvas_line.draw()

        # 饼图：分类占比（本月消费）
        labels, values = category_expense_share_for_month(month)
        self.fig_pie.clear()
        ax2 = self.fig_pie.add_subplot(111)
        if values and sum(values) > 0:
            wedges, texts, autotexts = ax2.pie(
                values,
                labels=labels,
                autopct="%1.1f%%",
                startangle=90,
                pctdistance=0.75,  # 百分比离中心更远（避免挤在一起）
                labeldistance=1.1,  # 标签放在扇形外（解决重叠）
            )

            # ✅ 自动调整字体大小（避免挤）
            for t in texts + autotexts:
                t.set_fontsize(9)

            ax2.axis('equal')  # 保持圆形
            self.fig_pie.tight_layout()
            # self.canvas_pie.draw()
            # ax2.pie(
            #     values,
            #     labels=labels,
            #     autopct="%1.1f%%",
            #     startangle=90,
            #     pctdistance=0.8,  # 百分比离中心远一点
            #     labeldistance=1.15,  # 标签放到扇形外面
            # )
            # ax2.axis('equal')
        else:
            ax2.text(0.5, 0.5, "本月无消费", ha='center', va='center', fontsize=12)
            ax2.axis('off')
        self.canvas_pie.draw()

    def selected_id(self):
        idxs = self.table.selectionModel().selectedRows()
        if not idxs: return None, None
        row = idxs[0].row()
        tid = int(self.table.item(row, 0).text())
        data = {
            "type": self.table.item(row, 1).text(),
            "category": self.table.item(row, 2).text(),
            "amount": float(self.table.item(row, 3).text()),
            "date": self.table.item(row, 4).text(),
            "note": self.table.item(row, 5).text() or None
        }
        return tid, data

    def on_add(self):
        dlg = TxnDialog(self)
        if dlg.exec() == QDialog.Accepted:
            v = dlg.get_value()
            if v:
                insert_txn(v)
                self.refresh()

    def on_edit(self):
        tid, data = self.selected_id()
        if tid is None:
            QMessageBox.information(self, "提示", "请先选中一条记录")
            return
        dlg = TxnDialog(self, data)
        if dlg.exec() == QDialog.Accepted:
            v = dlg.get_value()
            if v:
                update_txn(tid, v)
                self.refresh()

    def on_del(self):
        tid, _ = self.selected_id()
        if tid is None:
            QMessageBox.information(self, "提示", "请先选中一条记录")
            return
        if QMessageBox.question(self, "确认", "确定删除选中记录？") == QMessageBox.Yes:
            delete_txn(tid)
            self.refresh()

    def on_export(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出 CSV", f"账目_{self.current_month_str()}.csv", "CSV Files (*.csv)")
        if not path: return
        month = self.current_month_str()
        rows = list_txns(month)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["类型","分类","金额","日期","备注"])
            for r in rows:
                w.writerow([r["type"], r["category"], r["amount"], r["date"], r["note"] or ""])
        QMessageBox.information(self, "完成", "已导出 CSV")

    def on_cat_mgr(self):
        dlg = CategoryManagerDialog(self)
        dlg.exec()
        self.refresh()

# ---------- 入口 ----------
if __name__ == "__main__":
    init_db()
    app = QApplication([])
    win = MainWindow()
    win.show()
    app.exec()
