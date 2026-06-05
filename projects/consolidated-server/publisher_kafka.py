"""
Consolidated-server -> SHR publisher, Kafka-backbone variant (Pattern A).

The CDC reader produces a patient-changed event per binlog row to a Kafka topic.
This consumer streams those events and, for each changed patient, reuses the
fhir2 publish path (fetch patient FHIR from the EMR -> POST /SHR/fhir -> enroll
in OpenCR). Kafka decouples the CDC reader from the SHR write: if the SHR is
down or slow, events buffer in Kafka and are processed when it recovers, and the
consumer-group offset is the durable "what's been published" position.

Delivery is at-least-once: offsets commit only after a batch fully succeeds, and
the SHR writes are idempotent (PUT by uuid), so reprocessing is safe.

Global (non-patient-scoped) resources are synced on startup and then periodically
(they aren't carried as per-patient events).
"""
import json
import os
import sys
import time
import logging

from publisher import connect_dst
import publisher_fhir2 as P

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("publisher-kafka")


def env(name, default=None):
    return os.environ.get(name, default)


KAFKA_BROKERS = [b.strip() for b in env("KAFKA_BROKERS", "kafka:9092").split(",") if b.strip()]
KAFKA_TOPIC = env("KAFKA_TOPIC", "fhir.patient.changed")
KAFKA_GROUP = env("KAFKA_GROUP", "consolidated-shr")
GLOBAL_SYNC_INTERVAL = int(env("GLOBAL_SYNC_INTERVAL", "3600"))
POLL_MS = int(env("POLL_MS", "5000"))


def make_consumer():
    from kafka import KafkaConsumer
    return KafkaConsumer(
        KAFKA_TOPIC,
        bootstrap_servers=KAFKA_BROKERS,
        group_id=KAFKA_GROUP,
        enable_auto_commit=False,         # we commit only after a batch succeeds
        auto_offset_reset="earliest",     # don't miss events produced before we joined
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        consumer_timeout_ms=0,
    )


def main():
    # wait for the consolidated DB
    while True:
        try:
            conn = connect_dst()
            break
        except Exception as e:  # noqa: BLE001
            log.info("waiting for consolidated MySQL (%s)", e)
            time.sleep(3)
    # wait for Kafka
    consumer = None
    while consumer is None:
        try:
            consumer = make_consumer()
            log.info("consuming topic %s from %s (group %s)", KAFKA_TOPIC, KAFKA_BROKERS, KAFKA_GROUP)
        except Exception as e:  # noqa: BLE001
            log.info("waiting for Kafka (%s)", e)
            time.sleep(3)

    if P.SYNC_GLOBALS:
        try:
            P.sync_globals(conn)
        except Exception as e:  # noqa: BLE001
            log.error("initial global sync failed: %s", e)
    last_global = time.monotonic()

    while True:
        batches = consumer.poll(timeout_ms=POLL_MS, max_records=500)
        if batches:
            # dedupe (source_db, person_id) across the batch — many row events can
            # belong to the same patient; we only need to publish each once.
            keys = []
            seen = set()
            for _tp, msgs in batches.items():
                for m in msgs:
                    evt = m.value
                    k = (evt.get("source_db"), evt.get("person_id"))
                    if k[0] is None or k[1] is None or k in seen:
                        continue
                    seen.add(k)
                    keys.append(k)
            log.info("batch: %d event(s) -> %d unique patient(s)",
                     sum(len(v) for v in batches.values()), len(keys))
            all_ok = True
            for src, pid in keys:
                if not P.publish_patient(conn, src, pid):
                    all_ok = False
            if all_ok:
                consumer.commit()
            else:
                # leave offsets uncommitted so the batch is reprocessed (idempotent)
                log.warning("batch had failures; offsets NOT committed (will retry)")

        if P.SYNC_GLOBALS and time.monotonic() - last_global > GLOBAL_SYNC_INTERVAL:
            try:
                P.sync_globals(conn)
            except Exception as e:  # noqa: BLE001
                log.error("periodic global sync failed: %s", e)
            last_global = time.monotonic()


if __name__ == "__main__":
    sys.exit(main())
