from PyQt6.QtWidgets import QWidget, QGridLayout
from PyQt6.QtCore import Qt, QTimer, QParallelAnimationGroup, QPropertyAnimation, QEasingCurve


class ResponsiveGridContainer(QWidget):
    """Container widget that reflows game banner widgets dynamically into columns based on window width"""
    def __init__(self, parent=None, card_width: int = 200, spacing: int = 15):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent; background-color: transparent;")
        self.card_width = card_width
        self.spacing = spacing
        self.widgets = []
        self._last_columns = None
        self._reflow_animating = False
        self._reflow_cards = []
        self._reflow_debounce = QTimer(self)
        self._reflow_debounce.setSingleShot(True)
        self._reflow_debounce.setInterval(110)
        self._reflow_debounce.timeout.connect(self.reflow)
        self.grid_layout = QGridLayout(self)
        self.grid_layout.setContentsMargins(15, 15, 15, 15)
        self.grid_layout.setSpacing(spacing)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

    def set_card_width(self, new_width: int):
        """Update card width for all children and reflow layout."""
        self.card_width = new_width
        for w in list(self.widgets):
            try:
                if hasattr(w, 'set_card_size'):
                    w.set_card_size(new_width)
            except (RuntimeError, AttributeError):
                pass
        self._schedule_reflow()

    def set_banner_widgets(self, widgets: list):
        # Hide and destroy previous widgets that are no longer active
        for old_w in list(self.widgets):
            if old_w not in widgets:
                try:
                    old_w.hide()
                    old_w.setParent(None)
                    old_w.deleteLater()
                except (RuntimeError, AttributeError):
                    pass
        self.widgets = widgets
        self.reflow()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._cancel_reflow_animation()
        self._schedule_reflow()

    def _schedule_reflow(self):
        """Coalesce rapid splitter/slider geometry updates into one reflow."""
        self._reflow_debounce.start()

    def _cancel_reflow_animation(self):
        """Snap back to layout ownership when the grid boundary itself changes."""
        if not self._reflow_animating:
            return
        for animation_name in ("_reflow_slide",):
            animation = getattr(self, animation_name, None)
            if animation is not None:
                animation.stop()
        self.grid_layout.setEnabled(True)
        self._reflow_animating = False
        self._reflow_cards = []
        available_width = max(1, self.width() - 20)
        cols = max(1, available_width // (self.card_width + self.spacing))
        self._last_columns = cols
        self._apply_reflow(cols)

    def reflow(self):
        try:
            # Let the current slide finish; the final resize is reapplied afterward.
            if self._reflow_animating:
                return
            if not self.widgets:
                self._last_columns = None
                return

            available_width = max(1, self.width() - 20)
            cols = max(1, available_width // (self.card_width + self.spacing))

            # Only animate the meaningful layout change: cards crossing a column boundary.
            if self._last_columns is not None and cols != self._last_columns and not self._reflow_animating:
                self._animate_reflow(cols)
                return

            self._last_columns = cols
            self._apply_reflow(cols)
        except (RuntimeError, AttributeError):
            pass

    def _apply_reflow(self, cols: int):
        """Apply the grid positions without triggering another transition."""
        try:
            # Clear layout items without double-destroying child widgets
            while self.grid_layout.count() > 0:
                item = self.grid_layout.takeAt(0)

            for index, widget in enumerate(self.widgets):
                try:
                    row = index // cols
                    col = index % cols
                    self.grid_layout.addWidget(widget, row, col)
                except (RuntimeError, AttributeError):
                    pass
        except (RuntimeError, AttributeError):
            pass

    def _animate_reflow(self, cols: int):
        """Slide only cards whose row/column position changes into place."""
        try:
            self._reflow_animating = True
            self._pending_columns = cols
            self._reflow_cards = [
                widget for index, widget in enumerate(self.widgets)
                if (index // self._last_columns, index % self._last_columns)
                != (index // cols, index % cols)
            ]
            old_positions = {widget: widget.pos() for widget in self._reflow_cards}
            # Prevent QGridLayout from painting the intermediate destination frame.
            self.setUpdatesEnabled(False)
            self._apply_reflow(self._pending_columns)
            self._last_columns = self._pending_columns
            self.grid_layout.activate()
            self.grid_layout.setEnabled(False)

            self._reflow_slide = QParallelAnimationGroup(self)
            for widget in self._reflow_cards:
                new_position = widget.pos()
                widget.move(old_positions[widget])
                animation = QPropertyAnimation(widget, b"pos", self._reflow_slide)
                animation.setDuration(520)
                animation.setStartValue(old_positions[widget])
                animation.setEndValue(new_position)
                animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
                self._reflow_slide.addAnimation(animation)
            self.setUpdatesEnabled(True)
            self.update()
            self._reflow_slide.finished.connect(self._finish_reflow_animation)
            self._reflow_slide.start()
        except (RuntimeError, AttributeError):
            self._finish_reflow_animation()

    def _finish_reflow_animation(self):
        try:
            self.grid_layout.setEnabled(True)
            self.grid_layout.activate()
            self._reflow_cards = []
            self._reflow_animating = False
        except (RuntimeError, AttributeError):
            pass
