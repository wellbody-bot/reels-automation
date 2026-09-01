"""
Phase 1 - Task 1-3: 유튜브 트렌드 수집 스크립트 v1

키워드별로 search.list(order=viewCount, publishedAfter=최근 N일)를 호출해
급상승 영상을 수집하고, videos.list로 조회수/좋아요/영상 길이를 보강한 뒤
JSON으로 저장하고 콘솔에 요약을 출력한다.

사용 예:
    python scripts/youtube_trends.py                       # config/keywords.txt 자동 사용
    python scripts/youtube_trends.py --keywords "간헐적단식,다이어트 브이로그" --days 7
    python scripts/youtube_trends.py --keywords-file other_keywords.txt --max-duration-sec 180
"""

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KEYWORDS = ["간헐적단식", "다이어트 브이로그", "저속노화 식단", "홈트 루틴"]

ISO8601_DURATION_RE = re.compile(
    r"P(?:(?P<days>\d+)D)?T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?"
)


def load_api_key() -> str:
    for env_path in (ROOT / "config" / ".env", ROOT / ".env"):
        if env_path.exists():
            load_dotenv(env_path)
            break
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        sys.exit(
            "YOUTUBE_API_KEY가 설정되어 있지 않습니다. "
            "config/.env.example을 config/.env로 복사하고 키를 채워주세요."
        )
    return key


def parse_duration_to_seconds(duration: str) -> int:
    m = ISO8601_DURATION_RE.match(duration)
    if not m:
        return 0
    parts = {k: int(v) if v else 0 for k, v in m.groupdict().items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


def search_keyword(youtube, keyword: str, published_after: str, max_results: int, region: str, lang: str):
    video_ids = []
    request = youtube.search().list(
        part="snippet",
        q=keyword,
        type="video",
        order="viewCount",
        publishedAfter=published_after,
        maxResults=min(max_results, 50),
        regionCode=region,
        relevanceLanguage=lang,
    )
    response = request.execute()
    for item in response.get("items", []):
        video_ids.append(item["id"]["videoId"])
    return video_ids


def fetch_video_details(youtube, video_ids: list[str]) -> list[dict]:
    results = []
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i : i + 50]
        response = youtube.videos().list(
            part="snippet,statistics,contentDetails", id=",".join(chunk)
        ).execute()
        for item in response.get("items", []):
            snippet = item["snippet"]
            stats = item.get("statistics", {})
            duration_sec = parse_duration_to_seconds(item["contentDetails"]["duration"])
            results.append(
                {
                    "video_id": item["id"],
                    "title": snippet.get("title"),
                    "channel": snippet.get("channelTitle"),
                    "channel_id": snippet.get("channelId"),
                    "published_at": snippet.get("publishedAt"),
                    "view_count": int(stats.get("viewCount", 0)),
                    "like_count": int(stats.get("likeCount", 0)) if "likeCount" in stats else None,
                    "comment_count": int(stats.get("commentCount", 0)) if "commentCount" in stats else None,
                    "duration_sec": duration_sec,
                    "url": f"https://www.youtube.com/watch?v={item['id']}",
                }
            )
    return results


def fetch_channel_subscribers(youtube, channel_ids: list[str]) -> dict[str, int | None]:
    unique_ids = sorted(set(channel_ids))
    subs_by_channel: dict[str, int | None] = {}
    for i in range(0, len(unique_ids), 50):
        chunk = unique_ids[i : i + 50]
        response = youtube.channels().list(part="statistics", id=",".join(chunk)).execute()
        for item in response.get("items", []):
            stats = item.get("statistics", {})
            hidden = stats.get("hiddenSubscriberCount", False)
            subs_by_channel[item["id"]] = None if hidden else int(stats.get("subscriberCount", 0))
    return subs_by_channel


def run(
    keywords: list[str],
    days: int,
    max_results: int,
    region: str,
    lang: str,
    max_duration_sec: int | None,
    min_duration_sec: int | None,
    max_subscribers: int | None,
):
    api_key = load_api_key()
    youtube = build("youtube", "v3", developerKey=api_key)

    published_after = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    all_results = {}
    for keyword in keywords:
        duration_note = ""
        if min_duration_sec is not None or max_duration_sec is not None:
            duration_note = f", 길이 {min_duration_sec or 0}~{max_duration_sec if max_duration_sec is not None else '∞'}초"
        print(f"\n=== 키워드: {keyword} (최근 {days}일, region={region}{duration_note}) ===")
        try:
            video_ids = search_keyword(youtube, keyword, published_after, max_results, region, lang)
        except HttpError as e:
            print(f"  [오류] search.list 실패: {e}")
            all_results[keyword] = []
            continue

        if not video_ids:
            print("  결과 없음")
            all_results[keyword] = []
            continue

        details = fetch_video_details(youtube, video_ids)
        if max_duration_sec is not None:
            details = [d for d in details if d["duration_sec"] < max_duration_sec]
        if min_duration_sec is not None:
            details = [d for d in details if d["duration_sec"] >= min_duration_sec]

        subs_by_channel = fetch_channel_subscribers(youtube, [d["channel_id"] for d in details if d["channel_id"]])
        for d in details:
            subs = subs_by_channel.get(d["channel_id"])
            d["subscriber_count"] = subs
            d["view_to_sub_ratio"] = round(d["view_count"] / subs, 2) if subs else None

        if max_subscribers is not None:
            # 구독자 수를 비공개(hiddenSubscriberCount)한 채널은 대형 채널 오탐 위험을 피해 제외
            details = [d for d in details if d["subscriber_count"] is not None and d["subscriber_count"] < max_subscribers]

        details.sort(key=lambda d: d["view_count"], reverse=True)
        all_results[keyword] = details

        for d in details[:10]:
            mins, secs = divmod(d["duration_sec"], 60)
            subs_note = f"구독자 {d['subscriber_count']:,}" if d["subscriber_count"] is not None else "구독자 비공개"
            ratio_note = f", 비율 {d['view_to_sub_ratio']}" if d["view_to_sub_ratio"] is not None else ""
            print(
                f"  [{d['view_count']:>10,}회] ({mins}:{secs:02d}) {d['title']!r} "
                f"- {d['channel']} ({subs_note}{ratio_note}) - {d['url']}"
            )

    return all_results


def build_briefing_markdown(results: dict, days: int, top_n: int = 10) -> str:
    generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# 유튜브 벤치마킹 브리핑 ({generated_at})",
        "",
        f"최근 {days}일 이내 게시, 조회수 순 상위 {top_n}개 (키워드별)",
        "",
    ]
    for keyword, videos in results.items():
        lines.append(f"## {keyword}")
        lines.append("")
        if not videos:
            lines.append("- 결과 없음")
            lines.append("")
            continue
        lines.append("| 순위 | 조회수 | 길이 | 제목 | 채널 | 구독자 | 조회수/구독자 | 링크 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for rank, v in enumerate(videos[:top_n], start=1):
            mins, secs = divmod(v["duration_sec"], 60)
            title = v["title"].replace("|", "\\|")
            subs = f"{v['subscriber_count']:,}" if v["subscriber_count"] is not None else "비공개"
            ratio = v["view_to_sub_ratio"] if v["view_to_sub_ratio"] is not None else "-"
            lines.append(
                f"| {rank} | {v['view_count']:,} | {mins}:{secs:02d} | {title} | {v['channel']} | {subs} | {ratio} | [보기]({v['url']}) |"
            )
        lines.append("")
    return "\n".join(lines)


def save_results(results: dict, output_dir: Path, days: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / f"youtube_trends_{timestamp}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    csv_path = output_dir / f"youtube_trends_{timestamp}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "keyword", "title", "channel", "subscriber_count", "view_to_sub_ratio",
                "published_at", "view_count", "like_count", "comment_count", "duration_sec", "url",
            ]
        )
        for keyword, videos in results.items():
            for v in videos:
                writer.writerow(
                    [
                        keyword,
                        v["title"],
                        v["channel"],
                        v["subscriber_count"],
                        v["view_to_sub_ratio"],
                        v["published_at"],
                        v["view_count"],
                        v["like_count"],
                        v["comment_count"],
                        v["duration_sec"],
                        v["url"],
                    ]
                )

    briefing_dir = output_dir / "briefings"
    briefing_dir.mkdir(parents=True, exist_ok=True)
    briefing_path = briefing_dir / f"briefing_{timestamp}.md"
    briefing_path.write_text(build_briefing_markdown(results, days), encoding="utf-8")

    return out_path, csv_path, briefing_path


def parse_args():
    parser = argparse.ArgumentParser(description="유튜브 트렌드 수집 스크립트 v1 (Phase 1 Task 1-3)")
    parser.add_argument("--keywords", type=str, help="콤마로 구분된 키워드 목록")
    parser.add_argument("--keywords-file", type=str, help="한 줄에 하나씩 키워드가 담긴 텍스트 파일 경로")
    parser.add_argument("--days", type=int, default=7, help="최근 N일 이내 게시된 영상만 (기본 7일)")
    parser.add_argument("--max-results", type=int, default=30, help="키워드당 최대 결과 수 (기본 30, 최대 50). 길이 필터로 걸러지는 만큼 여유있게 가져옴")
    parser.add_argument("--region", type=str, default="KR", help="지역 코드 (기본 KR)")
    parser.add_argument("--lang", type=str, default="ko", help="relevanceLanguage (기본 ko)")
    parser.add_argument(
        "--max-duration-sec",
        type=int,
        default=90,
        help="이 길이(초) 미만 영상만 남김. 기본 90초 — 벤치마킹은 '글감'뿐 아니라 후킹/전개/CTA "
        "구성까지 참고하는 게 목적이라, 너무 긴 라이브/방송 영상은 제외하고 실제 구성이 있는 "
        "쇼츠 위주로 좁힘. 필터 끄려면 매우 큰 값(예: 999999)을 주면 됨",
    )
    parser.add_argument(
        "--min-duration-sec",
        type=int,
        default=30,
        help="이 길이(초) 이상 영상만 남김. 기본 30초 — 자막카드형 초단문 영상은 "
        "구성 벤치마킹 가치가 낮아 제외",
    )
    parser.add_argument(
        "--max-subscribers",
        type=int,
        default=100_000,
        help="구독자 수가 이 값 미만인 채널의 영상만 남김. 기본 10만 — 조회수가 채널 영향력(팬덤)"
        "때문인지 글감/구성 때문인지 구분하기 위한 필터. 구독자 수를 비공개한 채널은 대형 채널일 "
        "위험을 피해 함께 제외됨. 필터 끄려면 매우 큰 값(예: 999999999)을 주면 됨",
    )
    parser.add_argument("--output-dir", type=str, default=str(ROOT / "output"))
    return parser.parse_args()


def load_keywords_file(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def resolve_keywords(args) -> list[str]:
    default_keywords_file = ROOT / "config" / "keywords.txt"

    if args.keywords:
        return [k.strip() for k in args.keywords.split(",") if k.strip()]
    if args.keywords_file:
        return load_keywords_file(Path(args.keywords_file))
    if default_keywords_file.exists():
        print(f"[안내] config/keywords.txt 사용")
        return load_keywords_file(default_keywords_file)

    print(f"[안내] 키워드 미지정, 코드 기본값 사용: {DEFAULT_KEYWORDS}")
    return DEFAULT_KEYWORDS


def main():
    args = parse_args()
    keywords = resolve_keywords(args)

    results = run(
        keywords=keywords,
        days=args.days,
        max_results=args.max_results,
        region=args.region,
        lang=args.lang,
        max_duration_sec=args.max_duration_sec,
        min_duration_sec=args.min_duration_sec,
        max_subscribers=args.max_subscribers,
    )

    json_path, csv_path, briefing_path = save_results(results, Path(args.output_dir), args.days)
    print(f"\n저장 완료:\n  JSON     : {json_path}\n  CSV      : {csv_path}\n  브리핑(MD): {briefing_path}")


if __name__ == "__main__":
    main()
