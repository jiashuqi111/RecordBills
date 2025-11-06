import os, sqlite3, csv
from datetime import date, datetime
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QDialog, QFormLayout,
    QLineEdit, QComboBox, QDateEdit, QMessageBox, QFileDialog
)
from PySide6.QtCore import Qt, QDate

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
"""

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
            # 计算月末
            y, m = map(int, month.split("-"))
            if m == 12:
                end = f"{y+1}-01-01"
            else:
                end = f"{y}-{m+1:02d}-01"
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
        self.cat_cb.addItems(["餐饮","交通","购物","居住","工资","其他"])
        if data: self.cat_cb.setCurrentText(data["category"])
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

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OneAccount (PC)")
        self.resize(820, 560)

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
        top.addStretch(1)
        top.addWidget(self.sum_label)

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
        self.sum_label.setText(f'本月 收入 {s["income"]:.2f} | 支出 {s["expense"]:.2f} | 结余 {s["balance"]:.2f}')

    def selected_id(self):
        idxs = self.table.selectionModel().selectedRows()
        if not idxs: return None, None
        row = idxs[0].row()
        tid = int(self.table.item(row, 0).text())
        # 读当前行其他字段用于“编辑”
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

if __name__ == "__main__":
    init_db()
    app = QApplication([])
    win = MainWindow()
    win.show()
    app.exec()
