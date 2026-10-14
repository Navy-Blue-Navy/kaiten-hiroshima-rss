import hashlib
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET


SOURCE_URL = "https://kaiten-heiten-24.com/category/hiroshima/"
OUTPUT_FILE = "kaiten_hiroshima.xml"

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def clean_text(text):
    return re.sub(r"\s+", " ", text).strip()


def make_guid(link):
    return hashlib.sha256(
        link.encode("utf-8")
    ).hexdigest()


def fetch_page(url):
    for attempt in range(1, 4):
        try:
            print(f"取得 {attempt}/3: {url}")

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30,
            )

            print(f"HTTP: {response.status_code}")

            response.raise_for_status()
            return response.text

        except requests.RequestException as e:
            print(f"取得失敗: {e}")

            if attempt < 3:
                print("10秒待って再試行します。")
                time.sleep(10)

    return None


def parse_date(text):
    match = re.search(
        r"(20\d{2})年"
        r"(\d{1,2})月"
        r"(\d{1,2})日",
        text,
    )

    if not match:
        return None

    try:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            12,
            0,
            0,
            tzinfo=JST,
        )

    except ValueError:
        return None


def extract_clean_title(text):
    text = clean_text(text)

    match = re.search(
        r"(【(?:開店|閉店)】.+)",
        text,
    )

    if not match:
        return None

    return clean_text(
        match.group(1)
    )


def load_existing_items():
    items = {}

    if not os.path.exists(OUTPUT_FILE):
        return items

    try:
        tree = ET.parse(OUTPUT_FILE)
        root = tree.getroot()

        for item in root.findall("./channel/item"):
            link = (
                item.findtext("link", "")
                .strip()
            )

            guid = (
                item.findtext("guid", "")
                .strip()
            )

            if not guid and link:
                guid = make_guid(link)

            if not guid:
                continue

            categories = []

            for category_element in item.findall(
                "category"
            ):
                if category_element.text:
                    category = clean_text(
                        category_element.text
                    )

                    if (
                        category
                        and category
                        not in categories
                    ):
                        categories.append(
                            category
                        )

            items[guid] = {
                "title": item.findtext(
                    "title",
                    "",
                ),
                "link": link,
                "description": item.findtext(
                    "description",
                    "",
                ),
                "pubDate": item.findtext(
                    "pubDate",
                    "",
                ),
                "guid": guid,
                "categories": categories,
            }

    except Exception as e:
        print(f"既存RSS読込失敗: {e}")

    return items


def find_article_container(link_element):
    current = link_element

    for _ in range(8):
        current = current.parent

        if current is None:
            break

        text = clean_text(
            current.get_text(
                " ",
                strip=True,
            )
        )

        has_date = re.search(
            r"20\d{2}年"
            r"\d{1,2}月"
            r"\d{1,2}日",
            text,
        )

        has_open_close = (
            "【開店】" in text
            or "【閉店】" in text
        )

        if (
            has_date
            and has_open_close
            and len(text) < 1000
        ):
            return current

    return link_element.parent


def extract_categories(container, title):
    categories = []

    for cat_link in container.find_all(
        "a",
        href=True,
    ):
        href = cat_link.get(
            "href",
            "",
        )

        cat_text = clean_text(
            cat_link.get_text(
                " ",
                strip=True,
            )
        )

        if not cat_text:
            continue

        if "/category/" not in href:
            continue

        if cat_text == title:
            continue

        if cat_text not in categories:
            categories.append(
                cat_text
            )

    return categories


def extract_articles(html):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    articles = {}

    for a in soup.find_all(
        "a",
        href=True,
    ):
        raw_text = clean_text(
            a.get_text(
                " ",
                strip=True,
            )
        )

        if (
            "【開店】" not in raw_text
            and "【閉店】" not in raw_text
        ):
            continue

        title = extract_clean_title(
            raw_text
        )

        if not title:
            continue

        link = urljoin(
            SOURCE_URL,
            a["href"],
        )

        parsed = urlparse(link)

        if (
            parsed.scheme not in (
                "http",
                "https",
            )
            or parsed.netloc
            != "kaiten-heiten-24.com"
        ):
            continue

        # カテゴリページそのものは除外
        if parsed.path.startswith(
            "/category/"
        ):
            continue

        container = find_article_container(
            a
        )

        container_text = clean_text(
            container.get_text(
                " ",
                strip=True,
            )
        )

        date = parse_date(
            container_text
        )

        if date is None:
            continue

        categories = extract_categories(
            container,
            title,
        )

        articles[link] = {
            "title": title,
            "link": link,
            "date": date,
            "categories": categories,
        }

    result = list(
        articles.values()
    )

    result.sort(
        key=lambda x: (
            x["date"],
            x["link"],
        ),
        reverse=True,
    )

    return result


def build_rss(
    existing_items,
    current_articles,
):
    merged = dict(
        existing_items
    )

    new_count = 0

    for article in current_articles:
        guid = make_guid(
            article["link"]
        )

        if guid not in merged:
            new_count += 1

        description_parts = []

        if article["categories"]:
            description_parts.append(
                "カテゴリ: "
                + ", ".join(
                    article["categories"]
                )
            )

        description_parts.append(
            "開店閉店.com 広島"
        )

        merged[guid] = {
            "title": article["title"],
            "link": article["link"],
            "description": " / ".join(
                description_parts
            ),
            "pubDate": format_datetime(
                article["date"]
            ),
            "guid": guid,
            "categories": article[
                "categories"
            ],
        }

    def get_pubdate(item):
        try:
            dt = parsedate_to_datetime(
                item["pubDate"]
            )

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=timezone.utc
                )

            return dt

        except Exception:
            return datetime(
                1970,
                1,
                1,
                tzinfo=timezone.utc,
            )

    sorted_items = sorted(
        merged.values(),
        key=get_pubdate,
        reverse=True,
    )

    rss = ET.Element(
        "rss",
        version="2.0",
    )

    channel = ET.SubElement(
        rss,
        "channel",
    )

    ET.SubElement(
        channel,
        "title",
    ).text = "開店閉店.com 広島"

    ET.SubElement(
        channel,
        "link",
    ).text = SOURCE_URL

    ET.SubElement(
        channel,
        "description",
    ).text = (
        "開店閉店.com「広島」の"
        "開店・閉店新着情報"
    )

    ET.SubElement(
        channel,
        "language",
    ).text = "ja"

    ET.SubElement(
        channel,
        "lastBuildDate",
    ).text = format_datetime(
        datetime.now(JST)
    )

    for data in sorted_items:
        item = ET.SubElement(
            channel,
            "item",
        )

        ET.SubElement(
            item,
            "title",
        ).text = data["title"]

        ET.SubElement(
            item,
            "link",
        ).text = data["link"]

        ET.SubElement(
            item,
            "description",
        ).text = data[
            "description"
        ]

        ET.SubElement(
            item,
            "pubDate",
        ).text = data[
            "pubDate"
        ]

        for category in data.get(
            "categories",
            []
        ):
            ET.SubElement(
                item,
                "category",
            ).text = category

        guid_element = ET.SubElement(
            item,
            "guid",
        )

        guid_element.set(
            "isPermaLink",
            "false",
        )

        guid_element.text = data[
            "guid"
        ]

    tree = ET.ElementTree(
        rss
    )

    ET.indent(
        tree,
        space="  ",
    )

    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )

    return (
        len(sorted_items),
        new_count,
    )


def main():
    existing_items = (
        load_existing_items()
    )

    print(
        f"既存RSS件数: "
        f"{len(existing_items)}"
    )

    html = fetch_page(
        SOURCE_URL
    )

    if html is None:
        if existing_items:
            print(
                "取得に失敗したため、"
                "既存XMLを維持します。"
            )
            return

        raise RuntimeError(
            "広島ページの取得に失敗しました。"
        )

    articles = extract_articles(
        html
    )

    print()
    print(
        f"現在ページの記事取得件数: "
        f"{len(articles)}"
    )

    print()
    print("=== 取得記事 ===")

    for i, article in enumerate(
        articles,
        1,
    ):
        print()
        print(
            f"{i:2d}. "
            f"{article['date'].strftime('%Y-%m-%d')} "
            f"{article['title']}"
        )

        if article["categories"]:
            print(
                "    ["
                + ", ".join(
                    article["categories"]
                )
                + "]"
            )

        print(
            f"    {article['link']}"
        )

    if len(articles) < 5:
        if existing_items:
            print()
            print(
                "取得件数が異常に少ないため、"
                "既存XMLを維持します。"
            )
            return

        raise RuntimeError(
            f"取得件数が少なすぎます: "
            f"{len(articles)}"
        )

    total_count, new_count = (
        build_rss(
            existing_items,
            articles,
        )
    )

    print()
    print(
        f"今回の新規記事: "
        f"{new_count}"
    )

    print(
        f"RSS保存件数: "
        f"{total_count}"
    )

    print(
        f"保存先: "
        f"{os.path.abspath(OUTPUT_FILE)}"
    )


if __name__ == "__main__":
    main()