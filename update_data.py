"""노션 [EBITDA 총력전] Action Item DB를 읽어 data.json에 이번 주 점을 추가(같은 주면 덮어쓰기)한다.

필요한 환경변수
  NOTION_TOKEN        노션 통합(Internal integration) 토큰 — GitHub Secret으로 넣는다
  NOTION_DATA_SOURCE  (선택) 데이터소스 ID. 기본값은 Action Item DB
  NOTION_DATABASE     (선택) 데이터베이스 ID. 데이터소스 API가 실패할 때 대체 경로

계산 규칙 (Drop 제외 전체 항목 기준)
  expTotal    = Σ (예상) 월 EBITDA 임팩트                   → 최초 목표
  varAmt      = Σ [(실제) − (예상)]  (실제가 입력된 항목만)  → 조정 목표 = expTotal + varAmt
  actualCum   = Σ (실제)            (실제가 입력된 항목만)  → 빨간 선
  doneExp     = Σ (예상)  (상태 = 임팩트 적용 완료)          → 노란 선
"""
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
DATA_SOURCE = os.environ.get("NOTION_DATA_SOURCE", "3ceb77fa-2d76-80c0-beb6-000b75ba7c0a")
DATABASE = os.environ.get("NOTION_DATABASE", "3ceb77fa2d768023a9bec55775ec8f72")
STATUS_PROP = "상태"
EXP_PROP = "(예상) 월 EBITDA 임팩트"
ACT_PROP = "(실제) 월 EBITDA 임팩트"
DROP = "Drop"
DONE = "임팩트 적용 완료"
KST = dt.timezone(dt.timedelta(hours=9))


def notion_post(url, body, version):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": "Bearer " + os.environ["NOTION_TOKEN"],
            "Notion-Version": version,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch_all(url, version):
    rows, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        res = notion_post(url, body, version)
        rows += res["results"]
        if not res.get("has_more"):
            return rows
        cursor = res["next_cursor"]


def fetch_rows():
    try:
        return fetch_all("https://api.notion.com/v1/data_sources/%s/query" % DATA_SOURCE, "2025-09-03")
    except urllib.error.HTTPError as e:
        print("data_sources API 실패(%s) — databases API로 재시도" % e.code, file=sys.stderr)
        return fetch_all("https://api.notion.com/v1/databases/%s/query" % DATABASE, "2022-06-28")


def status_of(props):
    p = props.get(STATUS_PROP)
    if not p:
        raise SystemExit("'%s' 속성을 찾을 수 없음 — 노션 필드명이 바뀌었는지 확인" % STATUS_PROP)
    v = p.get(p["type"])
    return v.get("name") if isinstance(v, dict) else None


def number_of(props, name):
    p = props.get(name)
    if not p:
        raise SystemExit("'%s' 속성을 찾을 수 없음 — 노션 필드명이 바뀌었는지 확인" % name)
    t = p["type"]
    if t == "number":
        return p["number"]
    if t in ("formula", "rollup"):
        return p[t].get("number")
    return None


def week_label(today):
    """BBR 표기: <평일이 더 많은 달>월W<ISO 주차>. 예) 9/28~10/2 → 9월W40"""
    monday = today - dt.timedelta(days=today.weekday())
    months = [(monday + dt.timedelta(days=i)).month for i in range(5)]
    month = max(set(months), key=months.count)
    return "%d월W%d" % (month, today.isocalendar()[1])


def main():
    rows = [r for r in fetch_rows() if not r.get("in_trash") and not r.get("archived")]
    items = []
    for r in rows:
        props = r["properties"]
        st = status_of(props)
        if st == DROP:
            continue
        items.append((st, number_of(props, EXP_PROP) or 0, number_of(props, ACT_PROP)))
    if not items:
        raise SystemExit("노션에서 항목을 하나도 못 읽음 — 통합 연결(공유) 상태를 확인. data.json은 그대로 둠")

    today = dt.datetime.now(KST).date()
    point = {
        "label": week_label(today),
        "expTotal": round(sum(e for _, e, _ in items)),
        "varAmt": round(sum(a - e for _, e, a in items if a is not None)),
        "actualCum": round(sum(a for _, _, a in items if a is not None)),
        "actualCount": sum(1 for _, _, a in items if a is not None),
        "doneExp": round(sum(e for s, e, _ in items if s == DONE)),
        "doneCount": sum(1 for s, _, _ in items if s == DONE),
        "itemCount": len(items),
    }

    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)
    pts = data["points"]
    if len(pts) > 1 and pts[-1]["label"] == point["label"]:
        if pts[-1] == point:
            print("변경 없음:", point["label"])
            return
        pts[-1] = point          # 같은 주 재실행 → 덮어쓰기
    else:
        pts.append(point)        # 새 주 → 점 추가
    data["asOf"] = today.isoformat()
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("갱신:", json.dumps(point, ensure_ascii=False))


if __name__ == "__main__":
    main()
