# Chạy: docker cp ... && docker exec ... bash -c "odoo shell ... < /tmp/lms_repair_refresh.py"

from pathlib import Path

from odoo.addons.lms import csv_bootstrap, csv_runtime_sync

csv_runtime_sync._run_lms_post_import_sync(env.cr)
env["lms.data.integrity"].sudo().run_full_repair()
csv_dir = Path(csv_bootstrap.get_csv_import_dir())
digest = csv_runtime_sync.fingerprint_csv_bundle(csv_dir)
env["ir.config_parameter"].sudo().set_param(csv_runtime_sync.PARAM_BUNDLE_HASH, digest)
env.cr.commit()
print("LMS repair + hash OK", csv_dir)
