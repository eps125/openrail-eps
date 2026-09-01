#!/usr/bin/env python3
"""
openrail-eps allocation consumer.

Consumes the Rail Data Marketplace "NWR Passenger Train Allocation and Consist"
Kafka feed (TAF-TSI PassengerTrainConsistMessage XML) and writes a flattened
per-unit view into the `train_allocation` table, which livetrain.cgi renders as
the "Allocations for <date>" panel.

Message identity (verified against live data):
  Core (12 chars) = headcode(4) + CIF train UID(6) + origin departure hour(2)
  StartDate       = service date
  -> join key for livetrain is (CIF train UID, service date)

All configuration comes from the environment - see docker/bin/openrail-allocation.
"""
import os
import sys
import time
import datetime
import signal
import xml.etree.ElementTree as ET

import pymysql
from confluent_kafka import Consumer, KafkaException

NS = "{http://www.era.europa.eu/schemes/TAFTSI/5.3}"


def q(*path):
    return "/".join(NS + p for p in path)


def log(*a):
    print(f"[allocation {datetime.datetime.utcnow():%Y-%m-%dT%H:%M:%SZ}]", *a, flush=True)


def env(name, default=None, required=False):
    v = os.environ.get(name, default)
    if required and not v:
        log(f"FATAL: {name} is not set")
        sys.exit(1)
    return v


DDL = """
CREATE TABLE IF NOT EXISTS train_allocation (
  id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  cif_train_uid       CHAR(6)      NOT NULL,
  headcode            CHAR(4)      NOT NULL DEFAULT '',
  schedule_start_date DATE         NOT NULL,
  origin_tiploc       VARCHAR(8)   NOT NULL DEFAULT '',
  origin_dep          DATETIME     NULL,
  dest_tiploc         VARCHAR(8)   NOT NULL DEFAULT '',
  dest_arr            DATETIME     NULL,
  unit_no             VARCHAR(8)   NOT NULL,
  position            SMALLINT     NOT NULL DEFAULT 0,
  fleet_id            VARCHAR(12)  NOT NULL DEFAULT '',
  vehicles            VARCHAR(255) NOT NULL DEFAULT '',
  reported            DATETIME     NULL,
  message_id          VARCHAR(40)  NOT NULL DEFAULT '',
  updated             TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_unit (cif_train_uid, schedule_start_date, unit_no),
  KEY k_headcode (headcode, schedule_start_date),
  KEY k_date (schedule_start_date)
) ENGINE=InnoDB
"""


def _dt(s):
    """'2026-09-01T13:19:03' -> '2026-09-01 13:19:03' (or None)."""
    if not s:
        return None
    return s.replace("T", " ")[:19]


def parse_message(data):
    """Return dict for a PassengerTrainConsistMessage, or None to ignore."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        log("XML parse error:", e)
        return None
    if not root.tag.endswith("PassengerTrainConsistMessage"):
        return None

    reported = _dt(root.findtext(q("MessageHeader", "MessageReference", "MessageDateTime")))
    msg_id = root.findtext(q("MessageHeader", "MessageReference", "MessageIdentifier")) or ""

    core = start_date = None
    toi = root.find(q("TrainOperationalIdentification"))
    if toi is not None:
        t0 = toi.find(q("TransportOperationalIdentifiers"))
        if t0 is not None:
            core = (t0.findtext(q("Core")) or "").strip()
            start_date = t0.findtext(q("StartDate"))
    otn = (root.findtext(q("OperationalTrainNumberIdentifier", "OperationalTrainNumber")) or "").strip()

    headcode = (core[:4] if core else otn)[:4]
    uid = core[4:10] if core and len(core) >= 10 else None
    if not uid or not start_date:
        return None

    origin_tiploc = dest_tiploc = origin_dep = dest_arr = None
    units = {}  # unit_no -> {position, fleet, vehicles[]}

    for a in root.findall(q("Allocation")):
        if origin_tiploc is None:
            ol = a.find(q("TrainOriginLocation"))
            if ol is not None:
                origin_tiploc = ol.findtext(q("LocationSubsidiaryIdentification", "LocationSubsidiaryCode"))
            dl = a.find(q("TrainDestLocation"))
            if dl is not None:
                dest_tiploc = dl.findtext(q("LocationSubsidiaryIdentification", "LocationSubsidiaryCode"))
            origin_dep = _dt(a.findtext(q("TrainOriginDateTime")))
            dest_arr = _dt(a.findtext(q("TrainDestDateTime")))

        rg = a.find(q("ResourceGroup"))
        if rg is None:
            continue
        un = (rg.findtext(q("ResourceGroupId")) or "").strip()
        if not un:
            continue
        pos_txt = a.findtext(q("ResourceGroupPosition")) or ""
        pos = int(pos_txt) if pos_txt.isdigit() else 0
        fleet = (rg.findtext(q("FleetId")) or "").strip()
        veh = [v.findtext(q("VehicleId")) for v in rg.findall(q("Vehicle"))]
        veh = [v.strip() for v in veh if v and v.strip()]

        cur = units.get(un)
        if cur is None:
            units[un] = {"position": pos or 99, "fleet": fleet, "vehicles": veh}
        else:
            if pos and pos < cur["position"]:
                cur["position"] = pos
            for v in veh:
                if v not in cur["vehicles"]:
                    cur["vehicles"].append(v)
            if fleet and not cur["fleet"]:
                cur["fleet"] = fleet

    return {
        "uid": uid,
        "headcode": headcode,
        "start_date": start_date,
        "origin_tiploc": (origin_tiploc or "")[:8],
        "origin_dep": origin_dep,
        "dest_tiploc": (dest_tiploc or "")[:8],
        "dest_arr": dest_arr,
        "reported": reported,
        "message_id": msg_id[:40],
        "units": units,
    }


class DB:
    def __init__(self):
        self.kw = dict(
            host=env("DB_SERVER", "db"),
            user=env("DB_USER", "rail"),
            password=env("DB_PASSWORD", required=True),
            database=env("DB_NAME", "rail"),
            autocommit=True,
            connect_timeout=10,
            charset="utf8mb4",
        )
        self.conn = None
        self.connect()

    def connect(self):
        while True:
            try:
                self.conn = pymysql.connect(**self.kw)
                with self.conn.cursor() as c:
                    c.execute(DDL)
                log("database connected")
                return
            except Exception as e:  # noqa: BLE001
                log("database connect failed, retrying in 5s:", e)
                time.sleep(5)

    def apply(self, m):
        sql_del = "DELETE FROM train_allocation WHERE cif_train_uid=%s AND schedule_start_date=%s"
        sql_ins = (
            "INSERT INTO train_allocation "
            "(cif_train_uid, headcode, schedule_start_date, origin_tiploc, origin_dep, "
            " dest_tiploc, dest_arr, unit_no, position, fleet_id, vehicles, reported, message_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        )
        for attempt in (1, 2):
            try:
                with self.conn.cursor() as c:
                    c.execute(sql_del, (m["uid"], m["start_date"]))
                    for un, d in m["units"].items():
                        c.execute(sql_ins, (
                            m["uid"], m["headcode"], m["start_date"],
                            m["origin_tiploc"], m["origin_dep"],
                            m["dest_tiploc"], m["dest_arr"],
                            un[:8], d["position"], d["fleet"][:12],
                            " ".join(d["vehicles"])[:255], m["reported"], m["message_id"],
                        ))
                return
            except (pymysql.OperationalError, pymysql.InterfaceError) as e:
                log("db write failed (attempt %d): %s" % (attempt, e))
                self.connect()

    def purge(self, keep_days):
        try:
            with self.conn.cursor() as c:
                n = c.execute(
                    "DELETE FROM train_allocation "
                    "WHERE schedule_start_date < (CURDATE() - INTERVAL %d DAY)" % int(keep_days)
                )
            if n:
                log(f"purged {n} rows older than {keep_days} days")
        except Exception as e:  # noqa: BLE001
            log("purge failed:", e)


def main():
    bootstrap = env("RDM_KAFKA_BOOTSTRAP", required=True)
    topic = env("RDM_KAFKA_TOPIC", required=True)
    group = env("RDM_KAFKA_GROUP", required=True)
    user = env("RDM_KAFKA_USER", required=True)
    password = env("RDM_KAFKA_PASSWORD", required=True)
    offset_reset = env("RDM_KAFKA_OFFSET_RESET", "latest")
    keep_days = int(env("ALLOCATION_KEEP_DAYS", "21"))

    db = DB()

    conf = {
        "bootstrap.servers": bootstrap,
        "security.protocol": "SASL_SSL",
        "sasl.mechanisms": "PLAIN",
        "sasl.username": user,
        "sasl.password": password,
        "group.id": group,
        "auto.offset.reset": offset_reset,
        "enable.auto.commit": True,
        "auto.commit.interval.ms": 10000,
        "session.timeout.ms": 45000,
        "client.id": "openrail-eps-allocation",
    }
    c = Consumer(conf)
    c.subscribe([topic])
    log(f"subscribed to {topic} on {bootstrap} as group {group} (offset reset: {offset_reset})")

    running = {"v": True}

    def stop(*_):
        running["v"] = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    n = ignored = errors = 0
    last_purge = None
    last_report = time.time()
    try:
        while running["v"]:
            msg = c.poll(1.0)

            today = datetime.date.today()
            if last_purge != today:
                db.purge(keep_days)
                last_purge = today

            if msg is None:
                if time.time() - last_report > 300:
                    log(f"alive: applied={n} ignored={ignored} errors={errors}")
                    last_report = time.time()
                continue
            if msg.error():
                errors += 1
                log("kafka error:", msg.error())
                time.sleep(2)
                continue

            try:
                parsed = parse_message(msg.value())
                if parsed is None:
                    ignored += 1
                else:
                    db.apply(parsed)
                    n += 1
                    if n % 200 == 0:
                        log(f"applied {n} (ignored {ignored}, errors {errors})")
            except Exception as e:  # noqa: BLE001
                errors += 1
                log("message handling error:", repr(e))
    finally:
        log(f"shutting down: applied={n} ignored={ignored} errors={errors}")
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
