"""
export_catalog.py - the control plane's view of available export destinations,
for the UI. The canonical sinks live in data-plane/export/sinks/; that tree
isn't on the control-api path (and pulls pyarrow/cloud SDKs), so this is a small
static mirror - display only. Keep the two in sync when you add a sink.
"""

EXPORT_SINKS = [
    {"name": "parquet", "display_name": "Parquet files", "offline": True,
     "requires": "an output directory; no cloud account needed"},
    {"name": "s3", "display_name": "Amazon S3", "offline": False,
     "requires": "boto3 + a bucket/key destination; AWS credentials via the standard boto3 chain, never entered in the UI"},
    {"name": "adls", "display_name": "Azure Data Lake Storage", "offline": False,
     "requires": "azure-storage-blob + a container/path destination; AZURE_STORAGE_CONNECTION_STRING or account URL + DefaultAzureCredential"},
    {"name": "snowflake", "display_name": "Snowflake", "offline": False,
     "requires": "snowflake-connector-python + account/user/password/warehouse/database/schema"},
    {"name": "databricks", "display_name": "Databricks (Delta)", "offline": False,
     "requires": "databricks-sql-connector + workspace host/http_path/token + UC catalog/schema/volume"},
    {"name": "fabric", "display_name": "Microsoft Fabric (OneLake Lakehouse)", "offline": False,
     "requires": "azure-identity + azure-storage-file-datalake + Fabric workspace/lakehouse + service principal"},
]
