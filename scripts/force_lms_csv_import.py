# Chạy: Get-Content scripts/force_lms_csv_import.py -Raw | docker exec -i odoo_app_8069 odoo shell -c /etc/odoo/odoo.conf -d lms --no-http
# (trong shell Odoo có sẵn biến `env`)

from pathlib import Path

from odoo.addons.lms import csv_bootstrap, csv_runtime_sync

csv_dir = csv_bootstrap.get_csv_import_dir()
csv_bootstrap.import_lms_from_csv_directory(
    env,
    csv_dir,
    safe_upsert=True,
    delete_missing_managed=False,
)
csv_runtime_sync._run_lms_post_import_sync(env.cr)
digest = csv_runtime_sync.fingerprint_csv_bundle(Path(csv_dir))
env["ir.config_parameter"].sudo().set_param(csv_runtime_sync.PARAM_BUNDLE_HASH, digest)
env["ir.config_parameter"].sudo().set_param("lms.csv_import_done", "true")
env.cr.commit()
print("LMS CSV import OK:", csv_dir)
