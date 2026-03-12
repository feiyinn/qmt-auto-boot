import os
import time
import logging
import re
import sys
import csv
import subprocess
from datetime import datetime
import pywinauto as pw
import win32gui
import win32con
from pywinauto.keyboard import send_keys

try:
    import ddddocr
except ImportError:
    ddddocr = None
    logging.warning(
        "未安装 ddddocr，图片验证码识别降级。可运行: uv pip install ddddocr"
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    force=True,
)

LOGIN_SUBMIT_DELAY_SECONDS = 30  # 无需输账号密码，但验证码渲染需要时间，加上延时
POST_REFRESH_DELAY_SECONDS = 2  # 刷新按钮点击后，一定要等一会验证码图片才会更新
PROCESS_NAME = "XtMiniQmt.exe"


def get_running_qmt_pids(process_name=PROCESS_NAME):
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
    def __init__(self, path, notify_func=None):
        self.path = path
        self.notify = notify_func
        self.app = None
        self.process_id = None
        self.ocr = ddddocr.DdddOcr(show_ad=False) if ddddocr else None

    def _title_looks_like_login(self, title):
        title = (title or "").strip()
        if not title:
            return False
        return bool(
            re.search(r"(中金|财富|CICCWM|\bQMT\b|\bMiniQmt\b)", title, re.IGNORECASE)
        )

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
            "MozillaWindowClass",  # 排除 Firefox
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
                    if (
                        self.process_id
                        and win.element_info.process_id == self.process_id
                    ):
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
            return edits
        except Exception as e:
            raise RuntimeError(f"无法枚举登录窗口输入框: {e}") from e

    def _get_login_button(self, dlg):
        try:
            buttons = dlg.descendants(control_type="Button")
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
            edit.click_input()
        except Exception:
            try:
                edit.set_focus()
            except Exception:
                pass

        try:
            edit.set_edit_text(str(text))
            logging.info("%s 已通过 set_edit_text 填充", field_name)
            return
        except Exception as e:
            pass

        edit.type_keys("^a{BACKSPACE}", set_foreground=True, vk_packet=False)
        time.sleep(0.2)
        edit.type_keys(
            str(text), with_spaces=True, set_foreground=True, vk_packet=False
        )
        logging.info("%s 已通过 type_keys 填充", field_name)

    def _click_login_button(self, dlg):
        self._activate_window(dlg)
        button = self._get_login_button(dlg)

        try:
            button.invoke()
            logging.info("已通过 invoke() 提交登录")
            return
        except Exception as e:
            pass

        try:
            self._activate_window(dlg)
            dlg.type_keys("{ENTER}", set_foreground=True, vk_packet=False)
            logging.info("已通过对话框 Enter 提交登录")
            return
        except Exception as e:
            pass

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
            pass

        send_keys("{TAB}")
        time.sleep(0.3)
        send_keys("{ENTER}")
        logging.info("已通过键盘触发登录按钮")

    def _find_refresh_captcha_control(self, dlg):
        candidates = []
        try:
            descendants = dlg.descendants()
        except Exception:
            return None

        # 在中金，控件都没有名字。
        # 刷新按钮 [PROBE 12] rect=(2072,1140,2160,1158)
        # 算术图片 [PROBE 11] rect=(1941,1129,2065,1173)
        # 特征：它是 Custom 类型，宽度约 80-90，高度约 15-25，处于输入框和图片的右边
        # 获取所有 Edit 控件用来做高度参考
        edits = []
        for control in descendants:
            try:
                if control.element_info.control_type == "Edit" and control.is_visible():
                    edits.append(control)
            except Exception:
                continue

        ref_top = 0
        if edits:
            # 取最下面的 Edit 作为验证码输入框参考
            ref_top = max(edits, key=lambda e: e.rectangle().top).rectangle().top

        for control in descendants:
            try:
                if not control.is_visible():
                    continue
                ctrl_type = control.element_info.control_type
                if ctrl_type in ("Custom", "Button", "Static"):
                    rect = control.rectangle()
                    # 优先看有没有文本带刷新的
                    name = (
                        control.element_info.name or control.window_text() or ""
                    ).strip()
                    if "刷新" in name or "换" in name:
                        candidates.append(control)
                        continue

                    # 识别其位置特征：长宽比较大，通常在右侧
                    if 40 <= rect.width() <= 120 and 10 <= rect.height() <= 40:
                        # 必须跟最下方的 Edit 大致同在一个水平线上 (偏差不超过 30 像素)
                        if ref_top == 0 or abs(rect.top - ref_top) < 30:
                            candidates.append(control)
            except Exception:
                continue

        if candidates:
            # 刷新按钮通常在最右侧
            return max(candidates, key=lambda c: c.rectangle().left)

        return None

    def _click_refresh_captcha(self, dlg, captcha_image_ctrl=None):
        self._activate_window(dlg)

        # 1. 尝试找明确的刷新按钮控件
        refresh_control = self._find_refresh_captcha_control(dlg)
        if refresh_control is not None:
            try:
                refresh_control.invoke()
                logging.info("已通过 invoke() 点击刷新验证码")
                return
            except Exception:
                pass
            try:
                self._activate_window(dlg)
                refresh_control.click_input()
                logging.info("已点击刷新验证码控件")
                return
            except Exception:
                pass

        # 2. 如果提供了验证码图片控件本身，点击它通常也是刷新
        if captcha_image_ctrl is not None:
            try:
                self._activate_window(dlg)
                captcha_image_ctrl.click_input()
                logging.info("已点击验证码图像刷新")
                return
            except Exception:
                pass

        logging.warning("未能成功点击刷新验证码")

    def _solve_arithmetic(self, expr_text):
        """解析并计算算术表达式文本"""
        expr_text = expr_text.strip()
        if not expr_text:
            return None

        # 匹配数字1、运算符、数字2
        # 支持常见中文和特殊符号作为运算符：+ - * / × ÷
        match = re.search(r"(\d+)\s*([+\-×÷*/])\s*(\d+)", expr_text)
        if not match:
            # 尝试 OCR 可能识别错的符号，如 '=' 被识别成 '-'
            match = re.search(r"(\d+)\s*([^0-9=]{1,2})\s*(\d+)", expr_text)
            if not match:
                return None

        num1 = int(match.group(1))
        op = match.group(2).strip()
        num2 = int(match.group(3))

        logging.info("提取到算术表达式: %s %s %s", num1, op, num2)

        try:
            if op in ("+", "十"):
                return num1 + num2
            elif op in ("-", "一"):  # OCR 可能把减号识别成'一'
                return num1 - num2
            elif op in ("*", "×", "x", "X"):
                return num1 * num2
            elif op in ("/", "÷"):
                return num1 // num2 if num2 != 0 else 0
            else:
                logging.warning("未知的运算符: %s", op)
                # 默认按加法盲猜
                return num1 + num2
        except Exception as e:
            logging.error("计算异常: %s", e)
            return None

    def _probe_login_dialog(self, dlg):
        """探测登录对话框的所有控件信息，用于调试"""
        logging.info("--- 开始探测登录对话框结构 ---")
        try:
            for i, control in enumerate(dlg.descendants()):
                try:
                    if not control.is_visible():
                        continue
                    ctrl_type = control.element_info.control_type
                    name = control.element_info.name or ""
                    try:
                        text = control.window_text() or ""
                    except Exception:
                        text = ""
                    rect = control.rectangle()
                    logging.info(
                        f"[PROBE {i}] type={ctrl_type:<10} name='{name}' text='{text}' rect=({rect.left},{rect.top},{rect.right},{rect.bottom})"
                    )
                except Exception:
                    continue
        except Exception as e:
            logging.error("探测控件失败: %s", e)
        logging.info("--- 探测结束 ---")

    def _read_arithmetic_captcha(self, dlg):
        """读取并识别算术验证码，支持直接读文本控件或 OCR 截图"""
        # 策略A：尝试直接在文本控件里找算术题
        # try:
        #     for control in (
        #         dlg.descendants(control_type="Text")
        #         + dlg.descendants(control_type="Static")
        #         + dlg.descendants(control_type="Image")
        #     ):
        #         try:
        #             name = control.element_info.name or ""
        #             try:
        #                 text = control.window_text() or ""
        #             except:
        #                 text = ""

        #             combined = f"{name} {text}"
        #             result = self._solve_arithmetic(combined)
        #             if result is not None:
        #                 logging.info(
        #                     "直接通过文本控件识别出验证码: %s -> 答案 = %s",
        #                     combined,
        #                     result,
        #                 )
        #                 return result, control
        #         except Exception:
        #             continue
        # except Exception as e:
        #     logging.warning("策略A(文本扫描)失败: %s", e)

        # 策略B：使用 OCR 截图识别
        if not self.ocr:
            logging.warning("缺少 ddddocr 库，无法进行图片识别")
            return None, None

        logging.info("文本控件未发现验证码，尝试基于坐标布局进行 OCR 截图识别...")
        try:
            # 中金布局：找跟最下面的 Edit 处在同一水平线上的图片
            captcha_candidates = []
            edits = dlg.descendants(control_type="Edit")
            visible_edits = [e for e in edits if e.is_visible()]
            ref_top = (
                max(visible_edits, key=lambda e: e.rectangle().top).rectangle().top
                if visible_edits
                else 0
            )

            for control in dlg.descendants():
                try:
                    if not control.is_visible():
                        continue

                    ctrl_type = control.element_info.control_type
                    if ctrl_type in ("Image", "Custom", "Pane", "Static"):
                        name = (control.element_info.name or "").strip()
                        if not name and not "验证码" in name and not "图片" in name:
                            rect = control.rectangle()
                            # 中金验证码图片约 124x44
                            if 80 < rect.width() < 180 and 20 < rect.height() < 80:
                                # 必须和验证码输入框近似同一水平高度
                                if ref_top == 0 or abs(rect.top - ref_top) < 30:
                                    captcha_candidates.append(control)
                except Exception:
                    continue

            if captcha_candidates:
                # 算术图片通常在所有的备选中靠左侧一点（相比刷新按钮），取 X 坐标最小的那个
                best_img_ctrl = min(
                    captcha_candidates, key=lambda c: c.rectangle().left
                )

                self._activate_window(dlg)
                img = best_img_ctrl.capture_as_image()
                ocr_text = self.ocr.classification(img)
                logging.info("OCR 识别结果: '%s'", ocr_text)

                result = self._solve_arithmetic(ocr_text)
                if result is not None:
                    logging.info("OCR 识别并计算成功: 答案 = %s", result)
                    return result, best_img_ctrl
                else:
                    logging.warning("OCR 原始结果无法解析为算术题: %s", ocr_text)
                    return None, best_img_ctrl
        except Exception as e:
            logging.error("OCR 识别验证码失败: %s", e)

        return None, None

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
            visible_buttons = [
                b for b in win.descendants(control_type="Button") if b.is_visible()
            ]
        except Exception:
            return False

        if not (1 <= len(visible_buttons) <= 4):
            return False

        if main_window is not None:
            try:
                rect = win.rectangle()
                area = max(0, rect.width()) * max(0, rect.height())
                main_rect = main_window.rectangle()
                main_area = max(1, main_rect.width() * main_rect.height())
                if area < main_area * 0.4:
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
            score += max(0, 1000 - rect.left)
            preferred.append((score, button))
            fallback.append((rect.top, rect.left, button))

        if preferred:
            best = max(preferred, key=lambda item: item[0])[1]
            return best

        best = min(fallback, key=lambda item: (item[0], item[1]))[2]
        return best

    def _find_confirm_button(self, process_ids, main_window=None, timeout=10):
        deadline = time.time() + timeout

        while time.time() < deadline:
            candidates = []
            for win in pw.Desktop(backend="uia").windows():
                try:
                    if self._is_likely_confirm_dialog(
                        win, process_ids=process_ids, main_window=main_window
                    ):
                        candidates.append(win)
                except Exception:
                    continue

            if candidates:
                dialog = candidates[0]
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
                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass

        try:
            win.set_focus()
        except Exception:
            pass

        time.sleep(0.5)

    def stop(self):
        try:
            qmt_pids = get_running_qmt_pids()
            if not qmt_pids:
                logging.info("未检测到运行中的 XtMiniQmt.exe，无需停止")
                return True

            logging.info("检测到运行中的 QMT 进程: %s", qmt_pids)
            target = self._pick_best_qmt_window(process_ids=qmt_pids)

            self._activate_window(target)
            hwnd = self._get_hwnd(target)
            if hwnd:
                win32gui.PostMessage(hwnd, win32con.WM_SYSCOMMAND, win32con.SC_CLOSE, 0)
            else:
                target.close()

            confirm_dialog, confirm_button = self._find_confirm_button(
                qmt_pids,
                main_window=target,
                timeout=6,
            )

            if confirm_button is not None and confirm_dialog is not None:
                self._activate_window(confirm_dialog)
                confirm_button.click_input()

            if self._wait_qmt_exit(qmt_pids, timeout=15):
                logging.info("QMT/miniQMT 已成功停止")
                return True

            os.system(f"taskkill /F /IM {PROCESS_NAME} /T >nul 2>&1")
            return True
        except Exception as e:
            logging.exception("停止 QMT 时发生异常: %s", e)
            return False

    def login(self, probe_only=False):
        try:
            logging.info("清理旧进程并启动 QMT...")
            if not os.path.exists(self.path):
                raise FileNotFoundError(f"QMT 客户端不存在: {self.path}")

            os.system(f"taskkill /F /IM {PROCESS_NAME} /T >nul 2>&1")
            time.sleep(2)

            self.app = pw.Application(backend="uia").start(
                self.path, wait_for_idle=False
            )
            self.process_id = self.app.process
            logging.info("QMT 启动命令已发送，launcher pid=%s", self.process_id)

            dlg = self._find_login_dialog(timeout=30)
            if not dlg.is_visible():
                raise TimeoutError("已找到登录窗口，但窗口当前不可见")

            logging.info("窗口已就绪，开始模拟登录操作...")

            if probe_only:
                self._probe_login_dialog(dlg)
                logging.info("由于是 probe 模式，直接退出不再登录。")
                return True

            edit_controls = self._get_edit_controls(dlg)
            if not edit_controls:
                raise RuntimeError("未在登录窗口中找到任何 Edit 输入框")

            # 在中金客户端，输入框可能只有1-2个（假设密码记住了）
            # 我们找最后一个 Edit 控件，通常是验证码
            captcha_edit = edit_controls[-1]

            logging.info(
                "等待验证码加载和自动填充 %s 秒...", LOGIN_SUBMIT_DELAY_SECONDS
            )
            time.sleep(LOGIN_SUBMIT_DELAY_SECONDS)

            self._click_refresh_captcha(dlg)
            try:
                captcha_edit.click_input()
            except Exception:
                pass
            logging.info("刷新验证码后等待 %s 秒...", POST_REFRESH_DELAY_SECONDS)
            time.sleep(POST_REFRESH_DELAY_SECONDS)

            max_retries = 3
            success_answer = None

            for attempt in range(1, max_retries + 1):
                logging.info("开始第 %d 次验证码识别尝试", attempt)
                answer, img_ctrl = self._read_arithmetic_captcha(dlg)

                if answer is not None:
                    success_answer = answer
                    break

                logging.warning("未能识别出算术表达式，点击刷新验证码重试...")
                self._click_refresh_captcha(dlg, captcha_image_ctrl=img_ctrl)
                try:
                    captcha_edit.click_input()
                except Exception:
                    pass
                time.sleep(POST_REFRESH_DELAY_SECONDS)

            if success_answer is None:
                raise RuntimeError("连续多次刷新依然无法识别验证码算术表达式！")

            self._fill_edit_control(captcha_edit, success_answer, "验证码输入框")
            time.sleep(0.5)

            self._click_login_button(dlg)
            logging.info("登录指令已发送，等待主界面加载...")

            if self.check_success():
                msg = f"{datetime.now()} QMT/miniQMT 登录成功"
                logging.info(msg)
                if self.notify:
                    self.notify(msg)
                return True
            else:
                raise Exception("超时未检测到主界面，登录可能失败")

        except Exception as e:
            error_msg = f"登录异常: {str(e)}"
            logging.exception(error_msg)
            if self.notify:
                self.notify(error_msg)
            return False

    def check_success(self):
        """检测登录后的主窗口是否出现"""
        for _ in range(15):  # 等待 15 秒
            try:
                if self._check_main_window_once(log_snapshot=True):
                    return True
            except Exception as e:
                logging.info("检查主界面时出现异常，继续重试: %s", e)
            time.sleep(1)
        return False


if __name__ == "__main__":
    logging.info("进入 zj_mini_start.py 主程序入口")
    action = sys.argv[1].lower() if len(sys.argv) > 1 else "start"

    bot = QMTAutoLogin(path=r"C:\中金财富QMT个人版交易端\bin.x64\XtMiniQmt.exe")

    if action == "start":
        sys.exit(0 if bot.login(probe_only=False) else 1)
    elif action == "probe":
        sys.exit(0 if bot.login(probe_only=True) else 1)
    elif action == "stop":
        sys.exit(0 if bot.stop() else 1)
    else:
        raise ValueError(f"不支持的动作: {action}，仅支持 start / stop / probe")
