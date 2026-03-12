import os
import time
import logging
import re
import sys
import csv
import subprocess
from datetime import datetime
from pathlib import Path
import pywinauto as pw
import yaml
import win32api
import win32con
import win32gui
import win32process
from pywinauto.keyboard import send_keys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    force=True,
)

LOCAL_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.local.yaml"
DEFAULT_ACCOUNT_KEY = "ACCOUNT_ID_PROD1"
LOGIN_SUBMIT_DELAY_SECONDS = 30
POST_REFRESH_DELAY_SECONDS = 1


def load_local_credentials(config_path=LOCAL_CONFIG_PATH, account_key=DEFAULT_ACCOUNT_KEY):
    if not config_path.exists():
        raise FileNotFoundError(f"本地配置文件不存在: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    if not isinstance(config, dict):
        raise ValueError(f"本地配置文件格式错误，顶层应为 dict: {config_path}")

    user = config.get(account_key)
    password = config.get("QMT_PASS")

    if not user:
        raise KeyError(f"本地配置缺少账号键: {account_key}")
    if not password:
        raise KeyError("本地配置缺少密码键: QMT_PASS")

    return str(user), str(password)


def get_running_qmt_pids(process_name="XtMiniQmt.exe"):
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="gbk",
            errors="ignore",
            check=False,
        )
    except Exception as e:
        logging.warning("读取 QMT 进程列表失败: %s", e)
        return []

    pids = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("INFO:"):
            continue
        try:
            row = next(csv.reader([line]))
            if len(row) < 2:
                continue
            if row[0].strip('"').lower() != process_name.lower():
                continue
            pids.append(int(row[1]))
        except Exception:
            continue
    return pids

class QMTAutoLogin:
    def __init__(self, path, user, password, notify_func=None):
        self.path = path
        self.user = user
        self.password = password
        self.notify = notify_func
        self.app = None
        self.process_id = None

    def _title_looks_like_login(self, title):
        title = (title or "").strip()
        if not title:
            return False
        return bool(re.search(r"(国金|交易端|迅投|\bQMT\b|\bMiniQmt\b)", title, re.IGNORECASE))

    def _is_obvious_non_qmt_window(self, win):
        try:
            title = (win.window_text() or "").strip().lower()
            class_name = (win.element_info.class_name or "").strip()
        except Exception:
            return False

        if "miniqmt_runner" in title or title.startswith("pwsh"):
            return True

        return class_name in {
            "CASCADIA_HOSTING_WINDOW_CLASS",
            "Chrome_WidgetWin_1",
            "CabinetWClass",
            "Shell_TrayWnd",
            "Progman",
        }

    def _has_login_like_controls(self, win):
        try:
            edit_count = len(win.descendants(control_type="Edit"))
            button_count = len(win.descendants(control_type="Button"))
            return edit_count >= 1 and button_count >= 1
        except Exception:
            return False

    def _find_login_dialog(self, timeout=30):
        """从桌面轮询真实弹出的登录窗体，而不是依赖启动器进程上的窗口。"""
        desktop = pw.Desktop(backend="uia")
        deadline = time.time() + timeout
        last_candidates = []

        while time.time() < deadline:
            candidates = []
            for win in desktop.windows():
                try:
                    if self._is_obvious_non_qmt_window(win):
                        continue

                    title = win.window_text().strip()

                    matched = False
                    if self.process_id and win.element_info.process_id == self.process_id:
                        matched = True
                    elif self._title_looks_like_login(title):
                        matched = True
                    elif self._has_login_like_controls(win):
                        matched = True

                    if matched:
                        candidates.append(win)
                except Exception:
                    continue

            if candidates:
                best = None
                for win in candidates:
                    try:
                        if win.is_visible():
                            best = win
                            break
                    except Exception:
                        continue
                best = best or candidates[0]
                try:
                    logging.info(
                        "检测到登录窗口: title=%r class=%s process=%s",
                        best.window_text(),
                        best.element_info.class_name,
                        best.element_info.process_id,
                    )
                except Exception:
                    logging.info("检测到登录窗口")
                return best

            snapshot = []
            for win in desktop.windows():
                try:
                    title = win.window_text().strip()
                    if title:
                        snapshot.append(title)
                except Exception:
                    continue
            if snapshot != last_candidates:
                last_candidates = snapshot
                logging.info("等待登录窗口出现，当前可见标题: %s", snapshot[:8])

            time.sleep(1)

        raise TimeoutError("未找到 QMT 登录窗口")

    def _get_edit_controls(self, dlg):
        try:
            edits = dlg.descendants(control_type="Edit")
            logging.info("登录窗口中检测到 %s 个 Edit 控件", len(edits))
            return edits
        except Exception as e:
            raise RuntimeError(f"无法枚举登录窗口输入框: {e}") from e

    def _get_login_button(self, dlg):
        try:
            buttons = dlg.descendants(control_type="Button")
            logging.info("登录窗口中检测到 %s 个 Button 控件", len(buttons))
            visible_buttons = []
            for button in buttons:
                try:
                    if button.is_visible():
                        visible_buttons.append(button)
                except Exception:
                    continue

            for button in visible_buttons:
                try:
                    if "登录" in (button.element_info.name or ""):
                        return button
                except Exception:
                    continue

            if visible_buttons:
                return visible_buttons[0]
            if buttons:
                return buttons[0]
            raise RuntimeError("未找到登录按钮")
        except Exception as e:
            raise RuntimeError(f"无法定位登录按钮: {e}") from e

    def _fill_edit_control(self, edit, text, field_name):
        try:
            edit.set_focus()
        except Exception:
            edit.click_input()

        try:
            edit.set_edit_text(text)
            logging.info("%s 已通过 set_edit_text 填充", field_name)
            return
        except Exception as e:
            logging.info("%s 不支持 set_edit_text，改用键盘输入: %s", field_name, e)

        edit.type_keys("^a{BACKSPACE}", set_foreground=True, vk_packet=False)
        time.sleep(0.2)
        edit.type_keys(text, with_spaces=True, set_foreground=True, vk_packet=False)
        logging.info("%s 已通过 type_keys 填充", field_name)

    def _type_into_focused_field(self, text, field_name):
        send_keys("^a{BACKSPACE}", pause=0.05)
        time.sleep(0.2)
        send_keys(text, with_spaces=True, pause=0.05)
        logging.info("%s 已通过当前焦点输入", field_name)

    def _focus_edit_control(self, edit, field_name):
        if edit is None:
            logging.info("%s 不存在，跳过聚焦", field_name)
            return

        try:
            edit.set_focus()
            logging.info("%s 已通过 set_focus() 重新聚焦", field_name)
            return
        except Exception as e:
            logging.info("%s set_focus() 失败，改用 click_input(): %s", field_name, e)

        try:
            edit.click_input()
            logging.info("%s 已通过 click_input() 重新聚焦", field_name)
        except Exception as e:
            logging.info("%s click_input() 聚焦失败: %s", field_name, e)

    def _click_login_button(self, dlg):
        self._activate_window(dlg)
        button = self._get_login_button(dlg)

        try:
            button.invoke()
            logging.info("已通过 invoke() 提交登录")
            return
        except Exception as e:
            logging.info("invoke() 提交登录失败，改用键盘触发: %s", e)

        try:
            self._activate_window(dlg)
            dlg.type_keys("{ENTER}", set_foreground=True, vk_packet=False)
            logging.info("已通过对话框 Enter 提交登录")
            return
        except Exception as e:
            logging.info("对话框 Enter 提交登录失败，改用按钮点击: %s", e)

        try:
            self._activate_window(dlg)
            button.set_focus()
        except Exception:
            pass

        try:
            button.click_input()
            logging.info("已点击登录按钮")
            return
        except Exception as e:
            logging.info("点击登录按钮失败，改用 Tab+Enter 兜底: %s", e)

        send_keys('{TAB}')
        time.sleep(0.3)
        send_keys('{ENTER}')
        logging.info("已通过键盘触发登录按钮")

    def _find_refresh_captcha_control(self, dlg):
        candidates = []
        try:
            descendants = dlg.descendants()
        except Exception:
            return None

        for control in descendants:
            try:
                if not control.is_visible():
                    continue
                name = (control.element_info.name or control.window_text() or "").strip()
                if "刷新验证码" in name:
                    candidates.append(control)
            except Exception:
                continue

        if not candidates:
            return None

        def control_score(control):
            try:
                rect = control.rectangle()
                return (rect.top, rect.left)
            except Exception:
                return (10**9, 10**9)

        return min(candidates, key=control_score)

    def _click_refresh_captcha(self, dlg, captcha_edit=None):
        self._activate_window(dlg)

        refresh_control = self._find_refresh_captcha_control(dlg)
        if refresh_control is not None:
            try:
                refresh_control.invoke()
                logging.info("已通过 invoke() 点击刷新验证码")
                return
            except Exception as e:
                logging.info("invoke() 点击刷新验证码失败，改用鼠标点击: %s", e)

            try:
                self._activate_window(dlg)
                refresh_control.click_input()
                logging.info("已点击刷新验证码控件")
                return
            except Exception as e:
                logging.info("点击刷新验证码控件失败，改用坐标点击: %s", e)

        if captcha_edit is None:
            logging.info("未定位到验证码输入框，跳过刷新验证码点击")
            return

        try:
            dlg_rect = dlg.rectangle()
            captcha_rect = captcha_edit.rectangle()
            click_points = [
                (captcha_rect.right + 18, captcha_rect.top + captcha_rect.height() // 2),
                (captcha_rect.right + 88, captcha_rect.top + captcha_rect.height() // 2),
            ]
            for x, y in click_points:
                rel_x = max(1, x - dlg_rect.left)
                rel_y = max(1, y - dlg_rect.top)
                self._activate_window(dlg)
                dlg.click_input(coords=(rel_x, rel_y), absolute=False)
                logging.info("已按验证码区域布局点击刷新验证码: screen=(%s,%s)", x, y)
                return
        except Exception as e:
            logging.info("坐标点击刷新验证码失败: %s", e)

    def _check_main_window_once(self, log_snapshot=False):
        target_pids = [self.process_id] if self.process_id else None
        windows = self._get_qmt_windows(process_ids=target_pids)
        snapshots = []

        for win in windows:
            try:
                if not win.is_visible():
                    continue
                title = (win.window_text() or "").strip()
                rect = win.rectangle()
                snapshot = (
                    f"title={title!r} class={win.element_info.class_name} "
                    f"pid={win.element_info.process_id} rect=({rect.left},{rect.top},{rect.right},{rect.bottom})"
                )
                snapshots.append(snapshot)
                if title and "登录" not in title:
                    logging.info("检测到 QMT 主界面窗口: %s", snapshot)
                    return True
            except Exception:
                continue

        if log_snapshot and snapshots:
            logging.info("等待主界面出现，当前 QMT 窗口候选: %s", snapshots[:5])
        return False

    def _get_qmt_windows(self, process_ids=None):
        process_ids = set(process_ids or get_running_qmt_pids())
        windows = []
        for win in pw.Desktop(backend="uia").windows():
            try:
                if self._is_obvious_non_qmt_window(win):
                    continue
                if process_ids and win.element_info.process_id in process_ids:
                    windows.append(win)
                    continue

                title = (win.window_text() or "").strip()
                if self._title_looks_like_login(title):
                    windows.append(win)
            except Exception:
                continue
        return windows

    def _pick_best_qmt_window(self, process_ids=None):
        candidates = self._get_qmt_windows(process_ids=process_ids)
        visible = []
        for win in candidates:
            try:
                if win.is_visible():
                    visible.append(win)
            except Exception:
                continue

        pool = visible or candidates
        if not pool:
            raise RuntimeError("未找到正在运行的 QMT 窗口")

        def area(win):
            try:
                rect = win.rectangle()
                return max(0, rect.width()) * max(0, rect.height())
            except Exception:
                return 0

        best = max(pool, key=area)
        logging.info(
            "选中 QMT 窗口用于关闭: title=%r class=%s process=%s",
            best.window_text(),
            best.element_info.class_name,
            best.element_info.process_id,
        )
        return best

    def _is_likely_confirm_dialog(self, win, process_ids, main_window=None):
        try:
            if not win.is_visible():
                return False
        except Exception:
            return False

        if self._is_obvious_non_qmt_window(win):
            return False

        try:
            visible_buttons = [b for b in win.descendants(control_type="Button") if b.is_visible()]
        except Exception:
            return False

        if not (1 <= len(visible_buttons) <= 4):
            return False

        try:
            title = (win.window_text() or "").strip()
            process_id = win.element_info.process_id
            class_name = win.element_info.class_name
            rect = win.rectangle()
            area = max(0, rect.width()) * max(0, rect.height())
        except Exception:
            return False

        if process_id in set(process_ids or []):
            return True

        if title and any(word in title for word in ("退出", "确认", "提示", "QMT")):
            return True

        if main_window is not None:
            try:
                main_rect = main_window.rectangle()
                main_area = max(1, main_rect.width() * main_rect.height())
                if area < main_area * 0.4 and class_name in {"Qt5QWindowIcon", "#32770"}:
                    return True
            except Exception:
                pass

        return False

    def _pick_confirm_button(self, dialog):
        accept_keywords = ("是", "确定", "退出", "确认", "Yes", "OK")
        reject_keywords = ("否", "取消", "No", "Cancel")
        buttons = []

        for idx, button in enumerate(dialog.descendants(control_type="Button")):
            try:
                if not button.is_visible():
                    continue
                rect = button.rectangle()
                name = (button.element_info.name or button.window_text() or "").strip()
                buttons.append((button, name, rect))
            except Exception:
                continue

        if not buttons:
            return None

        preferred = []
        fallback = []
        for button, name, rect in buttons:
            if any(word in name for word in reject_keywords):
                continue
            score = 0
            if any(word in name for word in accept_keywords):
                score += 100
            if name:
                score += 10
            score += max(0, 1000 - rect.left)
            preferred.append((score, button))
            fallback.append((rect.top, rect.left, button))

        if preferred:
            best = max(preferred, key=lambda item: item[0])[1]
            return best

        # 如果按钮名全空，通常左侧按钮是“是/确定”
        best = min(fallback, key=lambda item: (item[0], item[1]))[2]
        return best

    def _find_confirm_button(self, process_ids, main_window=None, timeout=10):
        deadline = time.time() + timeout

        while time.time() < deadline:
            candidates = []
            for win in pw.Desktop(backend="uia").windows():
                try:
                    if self._is_likely_confirm_dialog(win, process_ids=process_ids, main_window=main_window):
                        candidates.append(win)
                except Exception:
                    continue

            if candidates:
                dialog = None
                fg_info = self._foreground_window_info()
                for win in candidates:
                    hwnd = self._get_hwnd(win)
                    if hwnd and hwnd == fg_info["hwnd"]:
                        dialog = win
                        break
                dialog = dialog or candidates[0]

                try:
                    rect = dialog.rectangle()
                    logging.info(
                        "检测到确认对话框: title=%r class=%s pid=%s rect=(%s,%s,%s,%s)",
                        dialog.window_text(),
                        dialog.element_info.class_name,
                        dialog.element_info.process_id,
                        rect.left,
                        rect.top,
                        rect.right,
                        rect.bottom,
                    )
                except Exception:
                    logging.info("检测到确认对话框")

                self._activate_window(dialog)
                return dialog, self._pick_confirm_button(dialog)

            time.sleep(0.5)

        return None, None

    def _wait_qmt_exit(self, original_pids, timeout=15):
        deadline = time.time() + timeout
        original_pids = set(original_pids)
        while time.time() < deadline:
            current = set(get_running_qmt_pids())
            if not current.intersection(original_pids):
                return True
            time.sleep(1)
        return False

    def _get_hwnd(self, win):
        for attr in ("handle", "native_window_handle"):
            try:
                hwnd = getattr(win.element_info, attr, None)
                if hwnd:
                    return int(hwnd)
            except Exception:
                continue
        return None

    def _foreground_window_info(self):
        try:
            hwnd = win32gui.GetForegroundWindow()
            return {
                "hwnd": hwnd,
                "title": win32gui.GetWindowText(hwnd),
                "class": win32gui.GetClassName(hwnd),
            }
        except Exception:
            return {"hwnd": None, "title": "", "class": ""}

    def _window_still_exists(self, win):
        hwnd = self._get_hwnd(win)
        if hwnd:
            try:
                return bool(win32gui.IsWindow(hwnd))
            except Exception:
                return True
        try:
            return win.is_visible()
        except Exception:
            return False

    def _activate_window(self, win):
        hwnd = self._get_hwnd(win)

        try:
            if win.is_minimized():
                win.restore()
                time.sleep(0.5)
        except Exception:
            pass

        if hwnd:
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            except Exception:
                pass

            try:
                fg_hwnd = win32gui.GetForegroundWindow()
                current_tid = win32api.GetCurrentThreadId()
                target_tid = win32process.GetWindowThreadProcessId(hwnd)[0]
                fg_tid = win32process.GetWindowThreadProcessId(fg_hwnd)[0] if fg_hwnd else 0

                attached_fg = False
                attached_target = False
                try:
                    if fg_tid and fg_tid != current_tid:
                        win32process.AttachThreadInput(current_tid, fg_tid, True)
                        attached_fg = True
                    if target_tid and target_tid != current_tid:
                        win32process.AttachThreadInput(current_tid, target_tid, True)
                        attached_target = True

                    win32gui.BringWindowToTop(hwnd)
                    win32gui.SetForegroundWindow(hwnd)
                    win32gui.SetActiveWindow(hwnd)
                    try:
                        win32gui.SetFocus(hwnd)
                    except Exception:
                        pass
                finally:
                    if attached_target:
                        win32process.AttachThreadInput(current_tid, target_tid, False)
                    if attached_fg:
                        win32process.AttachThreadInput(current_tid, fg_tid, False)
            except Exception as e:
                logging.info("Win32 激活窗口失败，继续使用 pywinauto 方式: %s", e)

        try:
            win.set_focus()
        except Exception:
            pass

        try:
            win.click_input(coords=(50, 10), absolute=False)
        except Exception:
            pass

        time.sleep(0.5)

    def _get_close_button(self, win):
        buttons = []
        for button in win.descendants(control_type="Button"):
            try:
                if button.is_visible():
                    buttons.append(button)
            except Exception:
                continue

        if not buttons:
            return None

        def close_button_score(button):
            try:
                rect = button.rectangle()
                # 越靠右上角越像标题栏关闭按钮
                return (rect.top, -rect.right)
            except Exception:
                return (10**9, 0)

        best = min(buttons, key=close_button_score)
        try:
            rect = best.rectangle()
            logging.info(
                "选中关闭按钮候选: name=%r rect=(%s,%s,%s,%s)",
                best.element_info.name,
                rect.left,
                rect.top,
                rect.right,
                rect.bottom,
            )
        except Exception:
            logging.info("选中关闭按钮候选")
        return best

    def _request_graceful_close(self, win):
        self._activate_window(win)
        hwnd = self._get_hwnd(win)

        if hwnd:
            try:
                win32gui.PostMessage(hwnd, win32con.WM_SYSCOMMAND, win32con.SC_CLOSE, 0)
                logging.info("已向 QMT 窗口发送 SC_CLOSE 消息")
                return
            except Exception as e:
                logging.info("发送 SC_CLOSE 消息失败，改用标题栏关闭按钮: %s", e)

        try:
            close_button = self._get_close_button(win)
            if close_button is not None:
                close_button.click_input()
                logging.info("已点击 QMT 标题栏关闭按钮")
                return
        except Exception as e:
            logging.info("点击标题栏关闭按钮失败，改用 close(): %s", e)

        try:
            win.close()
            logging.info("已发送关闭主窗口指令")
            return
        except Exception as e:
            logging.info("窗口 close() 失败，停止优雅关闭尝试: %s", e)

    def _wait_for_confirm_dialog(self, process_ids, main_window, timeout=6, retry_interval=2):
        deadline = time.time() + timeout
        next_retry = time.time()
        attempt = 0

        while time.time() < deadline:
            if time.time() >= next_retry:
                attempt += 1
                logging.info("执行第 %s 次关闭请求", attempt)
                self._request_graceful_close(main_window)
                next_retry = time.time() + retry_interval

            confirm_dialog, confirm_button = self._find_confirm_button(
                process_ids,
                main_window=main_window,
                timeout=1,
            )
            if confirm_button is not None and confirm_dialog is not None:
                return confirm_dialog, confirm_button

        return None, None

    def _confirm_dialog(self, dialog, button):
        self._activate_window(dialog)

        try:
            button.invoke()
            logging.info("已通过 invoke() 触发确认按钮")
            time.sleep(0.8)
            if not self._window_still_exists(dialog):
                return True
        except Exception as e:
            logging.info("invoke() 触发确认按钮失败: %s", e)

        try:
            self._activate_window(dialog)
            button.click_input()
            logging.info("已通过 click_input() 触发确认按钮")
            time.sleep(0.8)
            if not self._window_still_exists(dialog):
                return True
        except Exception as e:
            logging.info("click_input() 触发确认按钮失败: %s", e)

        try:
            self._activate_window(dialog)
            dialog.type_keys("{ENTER}", set_foreground=True, vk_packet=False)
            logging.info("已通过对话框 Enter 触发确认按钮")
            time.sleep(0.8)
            if not self._window_still_exists(dialog):
                return True
        except Exception as e:
            logging.info("对话框 Enter 触发确认按钮失败: %s", e)

        return False

    def stop(self):
        try:
            qmt_pids = get_running_qmt_pids()
            if not qmt_pids:
                logging.info("未检测到运行中的 XtMiniQmt.exe，无需停止")
                return

            logging.info("检测到运行中的 QMT 进程: %s", qmt_pids)
            target = self._pick_best_qmt_window(process_ids=qmt_pids)
            confirm_dialog, confirm_button = self._wait_for_confirm_dialog(
                qmt_pids,
                main_window=target,
                timeout=6,
                retry_interval=2,
            )

            if confirm_button is not None and confirm_dialog is not None:
                if self._confirm_dialog(confirm_dialog, confirm_button):
                    logging.info("已确认关闭 QMT")
                else:
                    logging.info("确认按钮已触发多种方式，但对话框仍未消失")
            else:
                logging.info("仍未检测到确认弹窗，继续等待进程退出")

            if self._wait_qmt_exit(qmt_pids, timeout=15):
                logging.info("QMT/miniQMT 已成功停止")
                return

            logging.warning("QMT 未在超时内退出，执行强制结束进程")
            os.system("taskkill /F /IM XtMiniQmt.exe /T >nul 2>&1")
        except Exception as e:
            logging.exception("停止 QMT 时发生异常: %s", e)

    def login(self):
        try:
            logging.info("清理旧进程并启动 QMT...")
            if not os.path.exists(self.path):
                raise FileNotFoundError(f"QMT 客户端不存在: {self.path}")

            os.system("taskkill /F /IM XtMiniQmt.exe /T >nul 2>&1")
            time.sleep(2)

            # 启动应用
            self.app = pw.Application(backend='uia').start(self.path, wait_for_idle=False)
            self.process_id = self.app.process
            logging.info("QMT 启动命令已发送，launcher pid=%s", self.process_id)

            dlg = self._find_login_dialog(timeout=30)
            if not dlg.is_visible():
                raise TimeoutError("已找到登录窗口，但窗口当前不可见")
            
            logging.info("窗口已就绪，开始模拟登录操作...")

            edit_controls = self._get_edit_controls(dlg)
            if not edit_controls:
                raise RuntimeError("未在登录窗口中找到任何 Edit 输入框")

            # 1. 聚焦账号框并填充账号
            user_edit = edit_controls[0]
            self._fill_edit_control(user_edit, self.user, "账号框")
            time.sleep(0.5)

            # 2. 按真实键盘导航切到下一个焦点，再输入密码
            send_keys('{TAB}')
            time.sleep(0.5)
            self._type_into_focused_field(self.password, "密码框")
            time.sleep(0.5)

            # 3. 开机自启动场景下，先等待启动初始化完成，再手动触发验证码刷新
            captcha_edit = edit_controls[2] if len(edit_controls) >= 3 else None
            logging.info("等待验证码加载和自动填充 %s 秒...", LOGIN_SUBMIT_DELAY_SECONDS)
            time.sleep(LOGIN_SUBMIT_DELAY_SECONDS)
            self._click_refresh_captcha(dlg, captcha_edit=captcha_edit)
            self._focus_edit_control(captcha_edit, "验证码框")
            logging.info("刷新验证码后等待 %s 秒...", POST_REFRESH_DELAY_SECONDS)
            time.sleep(POST_REFRESH_DELAY_SECONDS)

            # 4. 提交登录
            self._click_login_button(dlg)
            logging.info("登录指令已发送，等待主界面加载...")

            # 5. 验证是否成功
            if self.check_success():
                msg = f"{datetime.now()} QMT/miniQMT 登录成功"
                logging.info(msg)
                if self.notify: self.notify(msg)
            else:
                raise Exception("超时未检测到主界面，登录可能失败")

        except Exception as e:
            error_msg = f"登录异常: {str(e)}"
            logging.exception(error_msg)
            if self.notify: self.notify(error_msg)

    def check_success(self):
        """检测登录后的主窗口是否出现"""
        for _ in range(15): # 等待 15 秒
            try:
                if self._check_main_window_once(log_snapshot=True):
                    return True
            except Exception as e:
                logging.info("检查主界面时出现异常，继续重试: %s", e)
            time.sleep(1)
        return False

# --- 调用 ---
if __name__ == "__main__":
    logging.info("进入 gz_mini_start.py 主程序入口")
    action = sys.argv[1].lower() if len(sys.argv) > 1 else "start"
    bot = QMTAutoLogin(
        path=r"C:\国金证券QMT交易端\bin.x64\XtMiniQmt.exe", 
        user="",
        password="",
    )

    if action == "start":
        user, password = load_local_credentials()
        bot.user = user
        bot.password = password
        logging.info("已从本地配置读取账号: %s", DEFAULT_ACCOUNT_KEY)
        bot.login()
    elif action == "stop":
        bot.stop()
    else:
        raise ValueError(f"不支持的动作: {action}，仅支持 start / stop")
