import sqlite3
import pandas as pd

from airflow.decorators import dag, task
from airflow.utils.dates import days_ago
from airflow.models.variable import Variable
from airflow.operators.dummy import DummyOperator
from airflow.providers.google.cloud.transfers.local_to_gcs import LocalFilesystemToGCSOperator
from airflow.providers.google.cloud.transfers.gcs_to_bigquery import GCSToBigQueryOperator
from airflow.providers.google.cloud.operators.dataflow import DataflowStartFlexTemplateOperator
from datetime import datetime, timedelta


BASE_PATH = Variable.get('BASE_PATH') or "/opt/airflow"
GOOGLE_CLOUD_CONN_ID = Variable.get("GOOGLE_CLOUD_CONN_ID")
BUCKET_NAME = Variable.get('BUCKET_NAME') or "23-06-05-final-test"
DATASET_ID = Variable.get("DATASET_ID") or "devops-simple.customer_invoice"
GOOGLE_OBJECT_NAME = Variable.get("GOOGLE_OBJECT_NAME") or "extract_transform_customer_invoice.csv"

INPUT_OBJECT_NAME = "invoice.db"
SQL_FILE = "query_invoice.sql"

DATA_PATH = f"{BASE_PATH}/data"
SQL_PATH = f"{BASE_PATH}/sql"
OUT_PATH = f"{BASE_PATH}/data/{GOOGLE_OBJECT_NAME}"

GCS_OBJECT_NAME = GOOGLE_OBJECT_NAME
BIGQUERY_TABLE_NAME = "bs_customer_invoice"


@dag(
    default_args={
        'owner': 'okza',
        'email': 'datokza@gmail.com',
        'email_on_failure': True
    },
    schedule_interval='0 4 * * *',
    start_date=days_ago(1),
    tags=['sqlite', 'customer']
)
def bs_customer_invoice_dag():

    start = DummyOperator(task_id='start')
    end = DummyOperator(task_id='end')

    @task()
    def extract_transform():
        print("extract transform")
        conn = sqlite3.connect(f"{DATA_PATH}/{INPUT_OBJECT_NAME}")
        with open(f"{SQL_PATH}/{SQL_FILE}") as query:
            df = pd.read_sql(query.read(), conn)
        df.to_csv(OUT_PATH, index=False, header=False)

    extract_transform_data = extract_transform()
    now = datetime.now()

    store_data_gcs = LocalFilesystemToGCSOperator(
        task_id='store_data_gcs',
        gcp_conn_id=GOOGLE_CLOUD_CONN_ID,
        src=OUT_PATH,
        dst=GCS_OBJECT_NAME,
        bucket=BUCKET_NAME
    )

    start_flex_template = DataflowStartFlexTemplateOperator(
            task_id="start_ingestion_job",
            retries=0,
            pool="dataflow",
            pool_slots=1,
            execution_timeout=timedelta(minutes=240), #change from 180 min to 240 min by Ray
            body={
                "launchParameter": {
                    # "containerSpecGcsPath": GCS_FLEX_TEMPLATE_PATH,
                    "jobName": f"my-attendance-job",
                    "parameters": {
                        "region": "us-central1",
                        "input": f"gs://{BUCKET_NAME}/extract_transform_customer_invoice.csv",
                        "output": f"gs://{BUCKET_NAME}/output/out",
                        "runner": "DataflowRunner",
                        "project": "devops-simple",
                        "temp_location": f"gs://{BUCKET_NAME}/temp/"
                    }
                }
            },
            wait_until_finished=True,
            location="us-central1",
        )
    

    load_data_bigquery = GCSToBigQueryOperator(
        task_id='load_data_bigquery',
        bucket=BUCKET_NAME,
        source_objects=[GCS_OBJECT_NAME],
        destination_project_dataset_table=f"{DATASET_ID}.{BIGQUERY_TABLE_NAME}",
        schema_fields=[  # based on https://cloud.google.com/bigquery/docs/schemas
            {'name': 'customer_id', 'type': 'INT64', 'mode': 'REQUIRED'},
            {'name': 'full_name', 'type': 'STRING', 'mode': 'NULLABLE'},
            {'name': 'company', 'type': 'STRING', 'mode': 'NULLABLE'},
            {'name': 'address', 'type': 'STRING', 'mode': 'NULLABLE'},
            {'name': 'city', 'type': 'STRING', 'mode': 'NULLABLE'},
            {'name': 'state', 'type': 'STRING', 'mode': 'NULLABLE'},
            {'name': 'country', 'type': 'STRING', 'mode': 'NULLABLE'},
            {'name': 'postal_code', 'type': 'STRING', 'mode': 'NULLABLE'},
            {'name': 'phone', 'type': 'STRING', 'mode': 'NULLABLE'},
            {'name': 'fax', 'type': 'STRING', 'mode': 'NULLABLE'},
            {'name': 'email', 'type': 'STRING', 'mode': 'NULLABLE'},
            {'name': 'invoice_id', 'type': 'INT64', 'mode': 'NULLABLE'},
            {'name': 'invoice_date', 'type': 'DATE', 'mode': 'NULLABLE'},
            {'name': 'billing_address', 'type': 'STRING', 'mode': 'NULLABLE'},
            {'name': 'billing_city', 'type': 'STRING', 'mode': 'NULLABLE'},
            {'name': 'billing_state', 'type': 'STRING', 'mode': 'NULLABLE'},
            {'name': 'billing_country', 'type': 'STRING', 'mode': 'NULLABLE'},
            {'name': 'billing_postal_code', 'type': 'STRING', 'mode': 'NULLABLE'},
            {'name': 'total', 'type': 'FLOAT64', 'mode': 'NULLABLE'},
        ],
        autodetect=False,
        # If the table already exists - overwrites the table data
        write_disposition='WRITE_TRUNCATE',
    )

    start >> extract_transform_data >> store_data_gcs >>start_flex_template
    start_flex_template >> load_data_bigquery >> end


bs_customer_invoice_dag_etl = bs_customer_invoice_dag()
