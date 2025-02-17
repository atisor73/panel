from __future__ import annotations

from typing import (
    TYPE_CHECKING, ClassVar, List, Mapping, Optional, Type,
)

import param

from ..models import Feed as PnFeed
from ..models.feed import ScrollButtonClick
from ..util import edit_readonly
from .base import Column

if TYPE_CHECKING:
    from bokeh.document import Document
    from bokeh.model import Model
    from pyviz_comms import Comm

    from ..viewable import Viewable


class Feed(Column):

    load_buffer = param.Integer(default=50, bounds=(0, None), doc="""
        The number of objects loaded on each side of the visible objects.
        When scrolled halfway into the buffer, the feed will automatically
        load additional objects while unloading objects on the opposite side.""")

    scroll = param.Boolean(default=True, doc="""
        Whether to add scrollbars if the content overflows the size
        of the container.""")

    visible_range = param.Range(readonly=True, doc="""
        Read-only upper and lower bounds of the currently visible feed objects.
        This list is automatically updated based on scrolling.""")

    _bokeh_model: ClassVar[Type[Model]] = PnFeed

    _direction = 'vertical'

    _rename: ClassVar[Mapping[str, str | None]] = {
        'objects': 'children', 'visible_range': 'visible_children',
        'load_buffer': None,
    }

    def __init__(self, *objects, **params):
        for height_param in ["height", "min_height", "max_height"]:
            if height_param in params:
                break
        else:
            # sets a default height to prevent infinite load
            params["height"] = 300

        super().__init__(*objects, **params)
        self._last_synced = None

    @param.depends("visible_range", "load_buffer", watch=True)
    def _trigger_get_objects(self):
        # visible start, end / synced start, end
        vs, ve = self.visible_range
        ss, se = self._last_synced
        half_buffer = self.load_buffer // 2

        top_trigger = (vs - ss) < half_buffer
        bottom_trigger = (se - ve) < half_buffer
        invalid_trigger = (
            # to prevent being trapped and unable to scroll
            ve - vs < self.load_buffer and
            ve - vs < len(self.objects)
        )
        if top_trigger or bottom_trigger or invalid_trigger:
            self.param.trigger("objects")

    @property
    def _synced_range(self):
        n = len(self.objects)
        if self.visible_range:
            return (
                max(self.visible_range[0] - self.load_buffer, 0),
                min(self.visible_range[-1] + self.load_buffer, n)
            )
        elif self.view_latest:
            return (max(n - self.load_buffer * 2, 0), n)
        else:
            return (0, min(self.load_buffer, n))

    def _get_model(
        self, doc: Document, root: Optional[Model] = None,
        parent: Optional[Model] = None, comm: Optional[Comm] = None
    ) -> Model:
        model = super()._get_model(doc, root, parent, comm)
        self._register_events('scroll_button_click', model=model, doc=doc, comm=comm)
        return model

    def _process_property_change(self, msg):
        if 'visible_children' in msg:
            visible = msg.pop('visible_children')
            for model, _ in self._models.values():
                refs = [c.ref['id'] for c in model.children]
                if visible and visible[0] in refs:
                    indexes = sorted(refs.index(v) for v in visible if v in refs)
                    break
            else:
                return super()._process_property_change(msg)
            offset = self._synced_range[0]
            n = len(self.objects)
            visible_range = [
                max(offset + indexes[0], 0),
                min(offset + indexes[-1], n)
            ]
            if visible_range[0] >= visible_range[1]:
                visible_range[0] = visible_range[1] - self.load_buffer
            msg['visible_range'] = tuple(visible_range)
        return super()._process_property_change(msg)

    def _process_param_change(self, msg):
        msg.pop('visible_range', None)
        return super()._process_param_change(msg)

    def _get_objects(
        self, model: Model, old_objects: List[Viewable], doc: Document,
        root: Model, comm: Optional[Comm] = None
    ):
        from ..pane.base import RerenderError, panel
        new_models, old_models = [], []
        self._last_synced = self._synced_range

        for i, pane in enumerate(self.objects):
            self.objects[i] = panel(pane)

        for obj in old_objects:
            if obj not in self.objects:
                obj._cleanup(root)

        current_objects = list(self.objects)
        ref = root.ref['id']
        for i in range(*self._last_synced):
            pane = current_objects[i]
            if pane in old_objects and ref in pane._models:
                child, _ = pane._models[root.ref['id']]
                old_models.append(child)
            else:
                try:
                    child = pane._get_model(doc, root, model, comm)
                except RerenderError as e:
                    if e.layout is not None and e.layout is not self:
                        raise e
                    e.layout = None
                    return self._get_objects(model, current_objects[:i], doc, root, comm)
            new_models.append(child)
        return new_models, old_models

    def _process_event(self, event: ScrollButtonClick) -> None:
        """
        Process a scroll button click event.
        """
        if not self.visible_range:
            return

        # need to get it all the way to the bottom rather
        # than the center of the buffer zone
        load_buffer = self.load_buffer
        with param.discard_events(self):
            self.load_buffer = 1

        n = len(self.objects)
        n_visible = self.visible_range[-1] - self.visible_range[0]
        with edit_readonly(self):
            # plus one to center on the last object
            self.visible_range = (max(n - n_visible + 1, 0), n)

        with param.discard_events(self):
            # reset the buffers and loaded objects
            self.load_buffer = load_buffer
