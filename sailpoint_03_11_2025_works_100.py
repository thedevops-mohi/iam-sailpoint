# import azure.functions as func   # ❌ removed

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings("ignore", message="each list item must be one of")


logging.basicConfig(
    level=logging.INFO,          # Show INFO and above
    format="%(asctime)s [%(levelname)s] %(message)s",
)

from sailpoint.v3.api import (
    roles_api, sources_api, workflows_api, transforms_api,
    access_profiles_api, service_desk_integration_api,
    identity_profiles_api
)
from sailpoint.beta.api import (
    sp_config_api, connector_rule_management_api,
)
from sailpoint.v3.api_client import ApiClient as V3ApiClient
from sailpoint.beta.api_client import ApiClient as BetaApiClient
from sailpoint.configuration import Configuration
from sailpoint.beta.models import ExportPayload

from git import Repo, Actor
from urllib.parse import quote


def export_config() -> None:
    logging.info("⏰ SailPoint export process started.")
    start_time = time.time()

    BASE_DIR = "."
    os.makedirs(BASE_DIR, exist_ok=True)

    configuration = Configuration()

    config_items = {
       "ROLE": [], "SOURCE": [], "WORKFLOW": [], "TRANSFORM": [],
       "ACCESS_PROFILE": [], "SERVICE_DESK_INTEGRATION": [],
       "IDENTITY_PROFILE": [], "RULE": [],
    }

    # -------------- Helper Functions -------------
    def safe(obj, *attrs):
        for attr in attrs:
            if hasattr(obj, attr):
                return getattr(obj, attr, None)
        return None

    def add_items(results, target):
        for item in results:
            target.append({"id": safe(item, "id"), "name": safe(item, "name")})

    def start_export_job(api, config_type, ids):
        payload = ExportPayload(
            description=f"Exporting {config_type}",
            include_types=[config_type],
            object_options={config_type: {"includedIds": ids}}
        )
        return api.export_sp_config(export_payload=payload).job_id

    def wait_for_completion(api, job_id, timeout=900, interval=15):
        elapsed = 0
        while elapsed < timeout:
            try:
                status = api.get_sp_config_export_status(id=job_id).status
                logging.info(f"⏳ Job {job_id} status: {status}")

                if status == "COMPLETE":
                    return True
                if status in ["FAILED", "CANCELLED"]:
                    raise Exception(f"Job {job_id} failed: {status}")

            except Exception as e:
                logging.warning(f"⚠️ Status check error: {e}")

            time.sleep(interval)
            elapsed += interval

        raise TimeoutError(f"Job {job_id} timed out after {timeout}s.")

    def save_json(obj, directory, name):
        os.makedirs(directory, exist_ok=True)
        safe_name = name.replace(" ", "_").replace("/", "_").replace("#", "_")
        path = os.path.join(directory, f"{safe_name}.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(obj.model_dump_json(indent=4))
        logging.info(f"📁 Saved '{name}' → {path}")

    def download_result(api, job_id, config_type, item_name, item_id):
        try:
            if config_type == "RULE":
                rule = connector_rule_management_api.ConnectorRuleManagementApi(api.api_client).get_connector_rule(id=item_id)
                save_json(rule, os.path.join(BASE_DIR, config_type), item_name)
                return

            result = api.get_sp_config_export(id=job_id)

            if not result.objects:
                logging.warning(f"⚠️ No objects for {config_type} '{item_name}'")
                return

            for obj in result.objects:
                obj_name = safe(obj.var_self, "name") or safe(obj, "name", "id") or "Unnamed"
                save_json(obj, os.path.join(BASE_DIR, config_type), obj_name)

        except Exception as e:
            logging.warning(f"❌ Failed to download {config_type} '{item_name}': {e}")

    def export_item(api, config_type, item):
        logging.info(f"\n🔄 Exporting {config_type}: {item['name']}")
        try:
            job_id = start_export_job(api, config_type, [item["id"]])
            if wait_for_completion(api, job_id):
                download_result(api, job_id, config_type, item["name"], item["id"])
        except Exception as e:
            logging.warning(f"❌ Failed {config_type} '{item['name']}': {e}")

    # ----------- Collect V3 Items -------------
    try:
        with V3ApiClient(configuration) as client:
            api_map = {
                "ROLE": roles_api.RolesApi(client).list_roles,
                "SOURCE": sources_api.SourcesApi(client).list_sources,
                "WORKFLOW": workflows_api.WorkflowsApi(client).list_workflows,
                "TRANSFORM": transforms_api.TransformsApi(client).list_transforms,
                "ACCESS_PROFILE": access_profiles_api.AccessProfilesApi(client).list_access_profiles,
                "SERVICE_DESK_INTEGRATION": service_desk_integration_api.ServiceDeskIntegrationApi(client).get_service_desk_integrations,
                "IDENTITY_PROFILE": identity_profiles_api.IdentityProfilesApi(client).list_identity_profiles,
            }

            for key, func_api in api_map.items():
                add_items(func_api(), config_items[key])

    except Exception as e:
        logging.warning(f"⚠️ Skipping V3 items: {e}")

    # ----------- Collect RULE Items (Beta API) -------------
    try:
        with BetaApiClient(configuration) as client:
            add_items(
                connector_rule_management_api.ConnectorRuleManagementApi(client).get_connector_rule_list(),
                config_items["RULE"]
            )
    except Exception as e:
        logging.warning(f"❌ Exception during Beta API extraction: {e}")

    # -------------- Parallel Export ---------------
    with BetaApiClient(configuration) as client:
        api = sp_config_api.SPConfigApi(client)
        tasks = []

        with ThreadPoolExecutor(max_workers=10) as executor:
            for config_type, items in config_items.items():
                for item in items:
                    tasks.append(executor.submit(export_item, api, config_type, item))

            for future in as_completed(tasks):
                future.result()

    elapsed_time = time.time() - start_time
    logging.info(f"✅ Export complete in {elapsed_time:.2f}s")


# Optional manual invocation
if __name__ == "__main__":
    export_config()
