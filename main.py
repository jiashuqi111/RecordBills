import os, sys, sqlite3, csv, shutil
from pathlib import Path
from datetime import date, datetime

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QDialog, QFormLayout,
    QLineEdit, QComboBox, QDateEdit, QMessageBox, QFileDialog,
    QListWidget, QAbstractItemView, QInputDialog
)
from PySide6.QtCore import Qt, QDate

# ========================
# 数据库位置（保持不变：当前目录）
# 如需固定到 %APPDATA%/OneAccountPC/one_account.db，参见我之前的说明替换 DB_PATH 部分即可
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

-- 新增：分类表
CREATE TABLE IF NOT EXISTS categories(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);
"""

DEFAULT_CATEGORIES = ["餐饮", "交通", "购物", "居住", "工资", "其他"]

# ---------- DB helpers ----------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as c:
        # 执行多条 DDL
        for stmt in DDL.strip().split(";"):
            s = stmt.strip()
            if s:
                c.execute(s)
        # 若分类表为空，则插入默认分类
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
        # 直接从下拉来源中删除：不会影响已存在的账单记录
        c.execute("DELETE FROM categories WHERE name=?", (name,))
        c.commit()

# ---------- 交易 CRUD ----------
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
        r = {"收入": 0.0, "支出": 0.0}
        for row in rows:
            r[row["type"]] = float(row["total"] or 0)
        r["balance"] = r["收入"] - r["支出"]
        return r

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
        # 说明：删除分类只影响“下拉选项”，不会删除账单里已有的文本
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
        self.type_cb.addItems(["支出","收入"])
        if data: self.type_cb.setCurrentText(data["type"])
        form.addRow("类型", self.type_cb)

        self.cat_cb = QComboBox()
        cats = list_categories()
        self.cat_cb.addItems(cats)
        # 如果是编辑旧账单，且分类已不在列表里，就临时加入，确保能显示/保存
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
        self.resize(860, 600)

        central = QWidget(); self.setCentralWidget(central)
        v = QVBoxLayout(central)

        # 顶部：月份选择 + 汇总
        top = QHBoxLayout()
        self.month_edit = QDateEdit()
        self.month_edit.setDisplayFormat("yyyy-MM")
        self.month_edit.setDate(QDate.currentDate())
        self.month_edit.setCalendarPopup(True)
        top.addWidget(QLabel("月份："))
        top.addWidget(self.month_edit)

        self.sum_label = QLabel("本月 收入 0.00 | 支出 0.00 | 结余 0.00")
        self.sum_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        # 分类管理按钮
        self.btn_cat_mgr = QPushButton("分类管理")

        top.addStretch(1)
        top.addWidget(self.sum_label)
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

        s = sum_month(month)
        self.sum_label.setText(f'本月 收入 {s["收入"]:.2f} | 支出 {s["支出"]:.2f} | 结余 {s["balance"]:.2f}')

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
        dlg.exec()  # 关闭即刷新主界面（影响新增/编辑的分类下拉）
        self.refresh()

# ---------- 入口 ----------
if __name__ == "__main__":
    init_db()
    app = QApplication([])
    win = MainWindow()
    win.show()
    app.exec()
