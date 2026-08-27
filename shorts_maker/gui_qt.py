from __future__ import annotations

import os
import sys
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QColor, QCloseEvent, QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from .ffmpeg_tools import find_ffmpeg
from .processor import BatchProcessor
from .settings import Settings, load_settings, save_settings


VIDEO_FILTER = "Видео (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts);;Все файлы (*.*)"


class ProcessingThread(QThread):
    log_message = Signal(str)
    progress_changed = Signal(int, int, str)
    completed = Signal(object)
    failed = Signal(str, str)

    def __init__(self, settings: Settings, app_dir: Path, sources: list[Path], banner: Path, output: Path, cancel: threading.Event):
        super().__init__()
        self.settings = settings
        self.app_dir = app_dir
        self.sources = sources
        self.banner = banner
        self.output = output
        self.cancel = cancel

    def run(self) -> None:
        try:
            processor = BatchProcessor(
                self.settings, self.app_dir, self.log_message.emit,
                self.progress_changed.emit, self.cancel,
            )
            self.completed.emit(processor.run(self.sources, self.banner, self.output))
        except Exception as exc:
            self.failed.emit(str(exc), traceback.format_exc())


class VideoShortsWindow(QMainWindow):
    def __init__(self, app_dir: Path) -> None:
        super().__init__()
        self.app_dir = app_dir
        self.sources: list[Path] = []
        self.worker: ProcessingThread | None = None
        self.cancel_event = threading.Event()
        self.w: dict[str, object] = {}
        self.setWindowTitle("Video Shorts Maker")
        self.resize(1020, 820)
        self.setMinimumSize(880, 700)
        self._build()
        self._load(load_settings())
        self._refresh_ffmpeg()

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 14, 16, 14)
        title = QLabel("Video Shorts Maker")
        title.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        layout.addWidget(title)
        hint = QLabel("Нарезка длинных видео, заголовок из имени файла, TikTok-субтитры и рекламная вставка.")
        hint.setStyleSheet("color: #555;")
        layout.addWidget(hint)

        sources_box = QGroupBox("1. Исходные видео")
        sources_layout = QHBoxLayout(sources_box)
        self.source_list = QListWidget()
        self.source_list.setMinimumHeight(100)
        sources_layout.addWidget(self.source_list, 1)
        buttons = QVBoxLayout()
        self._button(buttons, "Добавить…", self._add_sources)
        self._button(buttons, "Удалить", self._remove_sources)
        self._button(buttons, "Очистить", self._clear_sources)
        buttons.addStretch()
        sources_layout.addLayout(buttons)
        layout.addWidget(sources_box)

        paths = QGridLayout()
        paths.addWidget(QLabel("2. Рекламный MP4"), 0, 0)
        self.w["banner"] = QLineEdit()
        paths.addWidget(self.w["banner"], 0, 1)
        choose_banner = QPushButton("Выбрать…")
        choose_banner.clicked.connect(self._choose_banner)
        paths.addWidget(choose_banner, 0, 2)
        paths.addWidget(QLabel("3. Папка результата"), 1, 0)
        self.w["output_dir"] = QLineEdit()
        paths.addWidget(self.w["output_dir"], 1, 1)
        choose_output = QPushButton("Выбрать…")
        choose_output.clicked.connect(self._choose_output)
        paths.addWidget(choose_output, 1, 2)
        layout.addLayout(paths)

        tabs = QTabWidget()
        tabs.addTab(self._video_tab(), "Видео и баннер")
        tabs.addTab(self._captions_tab(), "Заголовок и субтитры")
        tabs.addTab(self._recognition_tab(), "Распознавание")
        tabs.addTab(self._system_tab(), "FFmpeg")
        layout.addWidget(tabs, 1)

        actions = QHBoxLayout()
        self.start_button = QPushButton("Начать обработку")
        self.start_button.setStyleSheet("font-weight: 600; padding: 7px 18px;")
        self.start_button.clicked.connect(self._start)
        actions.addWidget(self.start_button)
        self.stop_button = QPushButton("Остановить")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop)
        actions.addWidget(self.stop_button)
        open_button = QPushButton("Открыть папку")
        open_button.clicked.connect(self._open_output)
        actions.addWidget(open_button)
        actions.addStretch()
        self.status_label = QLabel("Готово к работе")
        actions.addWidget(self.status_label)
        layout.addLayout(actions)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(145)
        self.log_text.setFont(QFont("Consolas", 9))
        layout.addWidget(self.log_text)

    @staticmethod
    def _button(layout, text: str, callback) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    @staticmethod
    def _double(minimum: float, maximum: float, step: float = 1.0, decimals: int = 1) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setSingleStep(step)
        widget.setDecimals(decimals)
        return widget

    @staticmethod
    def _spin(minimum: int, maximum: int) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        return widget

    @staticmethod
    def _combo(values: list[str]) -> QComboBox:
        widget = QComboBox()
        widget.addItems(values)
        return widget

    def _video_tab(self) -> QWidget:
        content = QWidget()
        form = QFormLayout(content)
        self.w["part_length"] = self._double(5, 3600, 5)
        form.addRow("Длина одной части, сек.", self.w["part_length"])
        self.w["min_last_part"] = self._double(0, 300, 1)
        form.addRow("Не делать хвост короче, сек.", self.w["min_last_part"])
        self.w["insert_mode"] = self._combo(["percent", "fixed"])
        form.addRow("Момент вставки: режим", self.w["insert_mode"])
        self.w["insert_value"] = self._double(0, 3600, 1)
        form.addRow("Момент вставки: процент/секунда", self.w["insert_value"])
        banner_mode = QComboBox()
        banner_mode.addItem("Поверх продолжающегося видео (рекомендуется)", "overlay")
        banner_mode.addItem("Остановить видео и вставить отдельным сегментом", "pause")
        self.w["banner_mode"] = banner_mode
        form.addRow("Как показывать баннер", banner_mode)
        self.w["banner_width_percent"] = self._double(20, 100, 1)
        form.addRow("Ширина баннера, % кадра", self.w["banner_width_percent"])
        self.w["banner_position_percent"] = self._double(0, 100, 1)
        form.addRow("Центр баннера по высоте, %", self.w["banner_position_percent"])
        self.w["main_volume_during_banner"] = self._double(0, 1, .05, 2)
        form.addRow("Громкость основного видео во время баннера", self.w["main_volume_during_banner"])
        self.w["banner_volume"] = self._double(0, 4, .1, 2)
        form.addRow("Громкость баннера (1.0 = исходная)", self.w["banner_volume"])
        aspect = QComboBox()
        aspect.addItem("Весь кадр + размытый фон (рекомендуется)", "vertical_blur")
        aspect.addItem("Весь кадр + чёрные поля", "vertical_pad")
        aspect.addItem("Заполнить экран с обрезанием краёв", "vertical_crop")
        aspect.addItem("Исходные пропорции", "original")
        self.w["aspect_mode"] = aspect
        form.addRow("Формат кадра", self.w["aspect_mode"])
        self.w["resolution"] = self._combo(["1080x1920", "720x1280", "original"])
        form.addRow("Разрешение", self.w["resolution"])
        self.w["fps"] = self._combo(["30", "60", "source"])
        form.addRow("Кадров в секунду", self.w["fps"])
        self.w["quality"] = self._combo(["high", "balanced", "compact"])
        form.addRow("Качество", self.w["quality"])
        self.w["bitrate_mbps"] = self._double(0, 200, 1)
        form.addRow("Битрейт, Мбит/с (0 = авто)", self.w["bitrate_mbps"])
        self.w["use_nvenc"] = QCheckBox("Использовать NVIDIA NVENC с автоматическим переходом на CPU")
        form.addRow(self.w["use_nvenc"])
        tab = QScrollArea()
        tab.setWidgetResizable(True)
        tab.setWidget(content)
        return tab

    def _color_control(self, name: str) -> QWidget:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit()
        edit.setMaximumWidth(120)
        self.w[name] = edit
        row.addWidget(edit)
        button = QPushButton("Цвет…")
        button.clicked.connect(lambda: self._choose_color(name))
        row.addWidget(button)
        row.addStretch()
        return container

    def _captions_tab(self) -> QWidget:
        tab = QWidget()
        row = QHBoxLayout(tab)
        title_box = QGroupBox("Заголовок из имени файла")
        title_form = QFormLayout(title_box)
        self.w["title_position"] = self._combo(["top", "center"])
        title_form.addRow("Положение", self.w["title_position"])
        self.w["title_font_size"] = self._spin(8, 300)
        title_form.addRow("Размер шрифта", self.w["title_font_size"])
        self.w["title_margin"] = self._spin(0, 2000)
        title_form.addRow("Отступ, пикс.", self.w["title_margin"])
        title_form.addRow("Цвет", self._color_control("title_color"))
        sub_box = QGroupBox("Субтитры")
        sub_form = QFormLayout(sub_box)
        self.w["subtitle_position"] = self._combo(["bottom", "center"])
        sub_form.addRow("Положение", self.w["subtitle_position"])
        self.w["subtitle_font_size"] = self._spin(8, 300)
        sub_form.addRow("Размер шрифта", self.w["subtitle_font_size"])
        self.w["subtitle_margin"] = self._spin(0, 2000)
        sub_form.addRow("Отступ снизу, пикс.", self.w["subtitle_margin"])
        sub_form.addRow("Основной цвет", self._color_control("subtitle_color"))
        sub_form.addRow("Текущее слово", self._color_control("subtitle_highlight"))
        self.w["subtitle_max_words"] = self._spin(1, 12)
        sub_form.addRow("Слов во фразе", self.w["subtitle_max_words"])
        self.w["subtitle_max_chars"] = self._spin(8, 100)
        sub_form.addRow("Знаков во фразе", self.w["subtitle_max_chars"])
        row.addWidget(title_box)
        row.addWidget(sub_box)
        return tab

    def _recognition_tab(self) -> QWidget:
        content = QWidget()
        form = QFormLayout(content)
        self.w["recognition"] = self._combo(["local", "groq", "none"])
        form.addRow("Режим (local / groq / none)", self.w["recognition"])
        self.w["whisper_model"] = self._combo(["tiny", "base", "small", "medium", "large-v3"])
        form.addRow("Модель локального Whisper", self.w["whisper_model"])
        self.w["whisper_device"] = self._combo(["auto", "cuda", "cpu"])
        form.addRow("Устройство", self.w["whisper_device"])
        self.w["language"] = self._combo(["ru", "en"])
        form.addRow("Язык", self.w["language"])
        key = QLineEdit()
        key.setEchoMode(QLineEdit.EchoMode.Password)
        self.w["groq_api_key"] = key
        form.addRow("Groq API key", key)
        self.w["groq_model"] = self._combo(["whisper-large-v3-turbo", "whisper-large-v3"])
        form.addRow("Модель Groq для речи", self.w["groq_model"])
        self.w["smart_insert"] = QCheckBox("Выбирать самый интересный момент через Groq")
        form.addRow(self.w["smart_insert"])
        self.w["smart_range_start"] = self._double(0, 3600, 1)
        form.addRow("Начало диапазона анализа, сек.", self.w["smart_range_start"])
        self.w["smart_range_end"] = self._double(0, 3600, 1)
        form.addRow("Конец диапазона анализа, сек.", self.w["smart_range_end"])
        self.w["groq_analysis_model"] = QLineEdit()
        form.addRow("Модель Groq для анализа", self.w["groq_analysis_model"])
        note = QLabel("В готовый пакет включены Groq и локальный faster-whisper. При первом локальном запуске модель загрузится автоматически.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #555;")
        form.addRow(note)
        tab = QScrollArea()
        tab.setWidgetResizable(True)
        tab.setWidget(content)
        return tab

    def _system_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(QLabel("Путь к ffmpeg.exe или папке FFmpeg"))
        path_row = QHBoxLayout()
        self.w["ffmpeg_path"] = QLineEdit()
        path_row.addWidget(self.w["ffmpeg_path"], 1)
        browse = QPushButton("Выбрать ffmpeg.exe…")
        browse.clicked.connect(self._choose_ffmpeg)
        path_row.addWidget(browse)
        layout.addLayout(path_row)
        self.ffmpeg_status = QLabel()
        layout.addWidget(self.ffmpeg_status)
        buttons = QHBoxLayout()
        check = QPushButton("Проверить")
        check.clicked.connect(self._refresh_ffmpeg)
        buttons.addWidget(check)
        install = QPushButton("Установить FFmpeg в папку программы")
        install.clicked.connect(lambda: self._run_helper("Установить_FFmpeg.bat"))
        buttons.addWidget(install)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.w["keep_temp"] = QCheckBox("Не удалять временные файлы (для диагностики)")
        layout.addWidget(self.w["keep_temp"])
        layout.addStretch()
        return tab

    def _load(self, settings: Settings) -> None:
        for name, widget in self.w.items():
            if not hasattr(settings, name) or name == "banner":
                continue
            value = getattr(settings, name)
            if isinstance(widget, QLineEdit):
                widget.setText(str(value))
            elif isinstance(widget, QComboBox):
                if name in {"aspect_mode", "banner_mode"}:
                    index = widget.findData(str(value))
                    widget.setCurrentIndex(max(0, index))
                else:
                    widget.setCurrentText(str(value))
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                widget.setValue(value)
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
        output = self.w["output_dir"]
        assert isinstance(output, QLineEdit)
        if not output.text():
            output.setText(str(Path.home() / "Videos" / "Video Shorts Maker"))

    def _text(self, name: str) -> str:
        widget = self.w[name]
        if isinstance(widget, QLineEdit):
            return widget.text()
        if isinstance(widget, QComboBox):
            if name in {"aspect_mode", "banner_mode"}:
                return str(widget.currentData())
            return widget.currentText()
        raise TypeError(name)

    def _value(self, name: str):
        widget = self.w[name]
        if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            return widget.value()
        raise TypeError(name)

    def _checked(self, name: str) -> bool:
        widget = self.w[name]
        assert isinstance(widget, QCheckBox)
        return widget.isChecked()

    def _settings(self) -> Settings:
        return Settings(
            ffmpeg_path=self._text("ffmpeg_path"), output_dir=self._text("output_dir"),
            part_length=self._value("part_length"), min_last_part=self._value("min_last_part"),
            insert_mode=self._text("insert_mode"), insert_value=self._value("insert_value"),
            smart_insert=self._checked("smart_insert"),
            smart_range_start=self._value("smart_range_start"), smart_range_end=self._value("smart_range_end"),
            title_position=self._text("title_position"), title_font_size=self._value("title_font_size"),
            title_margin=self._value("title_margin"), title_color=self._text("title_color"),
            subtitle_position=self._text("subtitle_position"), subtitle_font_size=self._value("subtitle_font_size"),
            subtitle_margin=self._value("subtitle_margin"), subtitle_color=self._text("subtitle_color"),
            subtitle_highlight=self._text("subtitle_highlight"), subtitle_max_words=self._value("subtitle_max_words"),
            subtitle_max_chars=self._value("subtitle_max_chars"), banner_volume=self._value("banner_volume"),
            banner_mode=self._text("banner_mode"), banner_width_percent=self._value("banner_width_percent"),
            banner_position_percent=self._value("banner_position_percent"),
            main_volume_during_banner=self._value("main_volume_during_banner"),
            aspect_mode=self._text("aspect_mode"), resolution=self._text("resolution"), fps=self._text("fps"),
            quality=self._text("quality"), bitrate_mbps=self._value("bitrate_mbps"), use_nvenc=self._checked("use_nvenc"),
            recognition=self._text("recognition"), whisper_model=self._text("whisper_model"),
            whisper_device=self._text("whisper_device"), groq_api_key=self._text("groq_api_key"),
            groq_model=self._text("groq_model"), groq_analysis_model=self._text("groq_analysis_model"),
            language=self._text("language"), keep_temp=self._checked("keep_temp"),
        )

    def _add_sources(self) -> None:
        values, _ = QFileDialog.getOpenFileNames(self, "Выберите длинные видео", "", VIDEO_FILTER)
        for value in values:
            path = Path(value)
            if path not in self.sources:
                self.sources.append(path)
                self.source_list.addItem(str(path))

    def _remove_sources(self) -> None:
        rows = sorted({self.source_list.row(item) for item in self.source_list.selectedItems()}, reverse=True)
        for row in rows:
            self.source_list.takeItem(row)
            del self.sources[row]

    def _clear_sources(self) -> None:
        self.sources.clear()
        self.source_list.clear()

    def _choose_banner(self) -> None:
        value, _ = QFileDialog.getOpenFileName(self, "Выберите рекламный MP4", "", VIDEO_FILTER)
        if value:
            widget = self.w["banner"]
            assert isinstance(widget, QLineEdit)
            widget.setText(value)

    def _choose_output(self) -> None:
        value = QFileDialog.getExistingDirectory(self, "Папка для готовых роликов")
        if value:
            widget = self.w["output_dir"]
            assert isinstance(widget, QLineEdit)
            widget.setText(value)

    def _choose_ffmpeg(self) -> None:
        value, _ = QFileDialog.getOpenFileName(self, "Выберите ffmpeg.exe", "", "FFmpeg (ffmpeg.exe);;Все файлы (*.*)")
        if value:
            widget = self.w["ffmpeg_path"]
            assert isinstance(widget, QLineEdit)
            widget.setText(value)
            self._refresh_ffmpeg()

    def _choose_color(self, name: str) -> None:
        current = QColor(self._text(name))
        color = QColorDialog.getColor(current, self, "Выберите цвет")
        if color.isValid():
            widget = self.w[name]
            assert isinstance(widget, QLineEdit)
            widget.setText(color.name().upper())

    def _refresh_ffmpeg(self) -> None:
        found = find_ffmpeg(self._text("ffmpeg_path"), self.app_dir)
        if found:
            self.ffmpeg_status.setText(f"✓ Найден: {found[0]}")
            self.ffmpeg_status.setStyleSheet("color: #167A2E;")
        else:
            self.ffmpeg_status.setText("✗ FFmpeg не найден")
            self.ffmpeg_status.setStyleSheet("color: #A32626;")

    def _run_helper(self, filename: str) -> None:
        path = self.app_dir / filename
        if not path.exists():
            QMessageBox.critical(self, "Файл не найден", f"Не найден {filename} рядом с программой.")
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError as exc:
            QMessageBox.critical(self, "Ошибка запуска", str(exc))

    def _start(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        try:
            settings = self._settings()
            banner = Path(self._text("banner"))
            output = Path(settings.output_dir)
            if not self.sources:
                raise ValueError("Добавьте хотя бы одно исходное видео.")
            if not banner.is_file():
                raise ValueError("Выберите существующий рекламный видеофайл.")
            save_settings(settings)
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, "Проверьте настройки", str(exc))
            return
        self.cancel_event.clear()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.progress.setValue(0)
        self.status_label.setText("Подготовка…")
        self.log_text.append("Начало пакетной обработки")
        self.worker = ProcessingThread(settings, self.app_dir, list(self.sources), banner, output, self.cancel_event)
        self.worker.log_message.connect(self.log_text.append)
        self.worker.progress_changed.connect(self._progress)
        self.worker.completed.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _stop(self) -> None:
        self.cancel_event.set()
        self.stop_button.setEnabled(False)
        self.status_label.setText("Остановка после текущей операции…")

    def _progress(self, done: int, total: int, message: str) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self.status_label.setText(f"{done}/{total}: {message}")

    def _done(self, created: list[Path]) -> None:
        self._finish()
        self.status_label.setText(f"Готово: {len(created)} роликов")
        QMessageBox.information(self, "Обработка завершена", f"Создано роликов: {len(created)}\n\n{self._text('output_dir')}")

    def _failed(self, message: str, details: str) -> None:
        self.log_text.append(details)
        self._finish()
        self.status_label.setText("Ошибка")
        QMessageBox.critical(self, "Не удалось завершить обработку", message)

    def _finish(self) -> None:
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def _open_output(self) -> None:
        try:
            path = Path(self._text("output_dir"))
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError as exc:
            QMessageBox.critical(self, "Не удалось открыть папку", str(exc))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker and self.worker.isRunning():
            answer = QMessageBox.question(self, "Обработка идёт", "Остановить обработку и закрыть программу?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.cancel_event.set()
            self.worker.wait(3000)
        try:
            save_settings(self._settings())
        except Exception:
            pass
        event.accept()


def run_gui(app_dir: Path, self_test: bool = False) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Video Shorts Maker")
    app.setStyle("Fusion")
    window = VideoShortsWindow(app_dir)
    if self_test:
        QTimer.singleShot(150, app.quit)
    else:
        window.show()
    return app.exec()
