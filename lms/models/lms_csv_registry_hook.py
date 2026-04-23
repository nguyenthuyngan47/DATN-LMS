# -*- coding: utf-8 -*-

import logging

from odoo import api, models

from odoo.addons.lms import csv_runtime_sync

_logger = logging.getLogger(__name__)


class LmsCsvRegistryHook(models.AbstractModel):
    _name = "lms.csv.registry.hook"
    _description = "Hook đồng bộ CSV khi Odoo load registry (khởi động)"

    def _register_hook(self):
        super()._register_hook()
        try:
            csv_runtime_sync.sync_csv_bundle_if_needed(
                self.env, from_registry_hook=True
            )
        except Exception:  # noqa: BLE001
            _logger.exception("lms.csv.registry.hook: sync thất bại.")

    @api.model
    def cron_sync_csv_bundle(self):
        """Cron gọi định kỳ để đảm bảo DB luôn bắt kịp CSV khi có thay đổi."""
        csv_runtime_sync.sync_csv_bundle_if_needed(self.env, from_registry_hook=False)
        return True
