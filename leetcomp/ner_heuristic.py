from collections import Counter
from datetime import datetime, timedelta
import json
import re
from typing import Any, Dict, List, Pattern, Tuple

from loguru import logger

from leetcomp.models import Posts
from leetcomp.utils import session_scope


BASE_SALARY_RANGE_INDIA = (2_00_000, 100_00_000)
TOTAL_SALARY_RANGE_INDIA = (2_00_000, 200_00_000)
TOTAL_TO_BASE_MAX_RATIO = 2.5
INTERN_SALARY_RANGE_INDIA = (10_000, 2_00_000)

LABEL_SPECIFICATION = {
    "RE_COMPANY": re.compile(r"\*?\*?company\s?\*?\*?[:-]-?\s?\*?\*?(?P<label>[&\w\.\-\(\)\,\/\` ]+)"),
    "RE_ROLE": re.compile(r"title\s?(/level)?\s?[:-]-?\s?(?P<label>[&\w\.\-\/\+\#\,\(\)\` ]+)"),
    "RE_YOE": re.compile(
        r"((yrs|years\sof\s)(experience|exp)|yoe|(\\n|\btotal\s)experience)\s?[:-]-?\s?(?P<label>[\w\.\+\~\-\,\/\` ]+)"
    ),
    "RE_YOE_CLEAN": re.compile(r"(\d{1,2}(\.\d{1,2})?)\s?(yrs|years?)?(\s?(\d{1,2})\s?(months))?"),
    "RE_YOE_CLEAN_MONTHS": re.compile(r"^(\d{1,2})\s?months?$"),
    "RE_SALARY": re.compile(r"(salary|base|base pay)\s?[:-]-?\s?(?P<label>[\w\,\₹\$\.\/\-\(\)\`\\u20b9&#8377;\~ ]+)"),
    "RE_LOCATION": re.compile(r"location\s?[:-]-?\s?(?P<label>[\w\,\` ]+)"),
    "RE_SALARY_TOTAL": re.compile(
        r"\\ntot?al (1st year\s)?(comp[e|a]nsation|comp|ctc)(\sfor 1st year)?(\s?\(\s?(salary|base).+?\))?(?P<label>.+)"
    ),
    "RE_SALARY_CLEAN_LPA": re.compile(r"(\d{1,3}(\.\d{1,2})?)\s?(lpa|lakh|lac|l)"),
}

LOCATION_SPECIFICATION = {}

# update the location specification
with open("data/locations.json", "r") as f:
    location_data = json.load(f)

for country, cities in location_data.items():
    if cities:
        cities_regex = re.compile(r"[\(\s\,\/\|](?P<city>" + "|".join(cities.keys()) + ")")
    else:
        cities_regex = ""  # type: ignore
    LOCATION_SPECIFICATION[country] = cities_regex


def _preprocess_text(content: str) -> str:
    content = content.lower()
    content = re.sub(r"[\*\t\`]", " ", content)
    content = re.sub(r"\s+", " ", content).strip()
    content = re.sub(r"\\u20b9", "Rs", content)
    content = content.split("\n")[0]
    return content


def _find_matches(regex_pattern: Pattern[str], content: str) -> List[str]:
    matches = []
    for match in re.finditer(regex_pattern, content):
        matched_text = match.group("label").strip()
        formatted_text = " ".join([txt.capitalize() for txt in matched_text.split(" ")])
        # matches.append((match.start(), match.end(), formatted_text))
        matches.append(formatted_text)
    return matches


def _get_info_as_flat_list(
    companies: List[str], roles: List[str], yoes: List[str], pays: List[str], pays_t: List[str], info: Dict[str, Any]
) -> List[Dict[str, Any]]:
    n_info = min([len(companies), len(roles), len(yoes), len(pays)])
    expanded_info = []
    for _ in range(n_info):
        _info = info.copy()
        _info["company"] = companies[0]
        _info["role"] = roles[0]
        _info["yoe"] = yoes[0]
        _info["salary"] = pays[0]
        _info["salaryTotal"] = pays_t[0] if pays_t else ""
        expanded_info.append(_info)
    return expanded_info


def _get_clean_location(title: str, content: str) -> Tuple[str, str]:
    for country, cities_regex in LOCATION_SPECIFICATION.items():
        if cities_regex:
            for match in re.finditer(cities_regex, title):
                city = match.group("city")
                return (location_data[country][city], country)
    for match in re.finditer(LABEL_SPECIFICATION["RE_LOCATION"], content):
        location = "," + match.group("label")
        for country, cities_regex in LOCATION_SPECIFICATION.items():
            if cities_regex:
                for match in re.finditer(cities_regex, location):
                    city = match.group("city")
                    return (location_data[country][city], country)
        for country in LOCATION_SPECIFICATION.keys():
            if re.findall(r"[\(\s\,\/\|]" + country, location):
                return ("", country)
    for country in LOCATION_SPECIFICATION.keys():
        if re.findall(r"[\(\s\,\/\|]" + country, title):
            return ("", country)
    return ("", "")


def _get_clean_yoe(yoe: str, clean_title: str, role: str) -> float:
    if yoe in {"fresher", "new grad", "n/a", "none"}:
        return 0.0
    if "grad" in yoe:
        return 0.0
    for m in re.finditer(LABEL_SPECIFICATION["RE_YOE_CLEAN_MONTHS"], yoe):
        return round(float(m.groups()[0]) / 12, 1)
    if not yoe:
        if "intern" in clean_title or "intern" in role:
            return 0.0
    for m in re.finditer(LABEL_SPECIFICATION["RE_YOE_CLEAN"], yoe):
        groups = m.groups()
        return round(float(groups[0]) + (int(groups[4]) / 12 if groups[4] else 0), 1)
    return -1.0


def _get_clean_salary_for_india(salary: str) -> Tuple[float, str]:
    if "per month" in salary or "/month" in salary:
        for m in re.finditer(r"\d{4,6}", salary):
            return (int(float(m.group())), "monthly")
        for m in re.finditer(r"(\d{2})k ", salary):
            return (int(float(m.groups()[0]) * 1000), "monthly")
    for m in re.finditer(r"\d{6,7}", salary):
        return (int(float(m.group())), "yearly")
    for m in re.finditer(LABEL_SPECIFICATION["RE_SALARY_CLEAN_LPA"], salary):
        return (int(float(m.groups()[0]) * 1_00_000), "yearly")
    return (-1, "yearly")


def _report(raw_info: List[Dict[str, Any]]) -> None:
    logger.info(f"Posts with all the info: {len(raw_info)}")
    logger.info(f"Posts with Location: {len([r for r in raw_info if 'country' in r])}")
    logger.info(f"Posts with YOE: {len([r for r in raw_info if r['cleanYoe'] >= 0])}")
    logger.info(f"Posts from India: {len([r for r in raw_info if 'country' in r and r['country'] == 'india'])}")
    logger.info(
        f"Posts with Total Comp: {len([r for r in raw_info if 'cleanSalaryTotal' in r and r['cleanSalaryTotal'] != -1.0])}"
    )


def _is_valid_yearly_base_pay_from_india(base_pay: float):
    return base_pay >= BASE_SALARY_RANGE_INDIA[0] and base_pay <= BASE_SALARY_RANGE_INDIA[1]


def _is_valid_monthly_internship_pay_from_india(base_pay: float):
    return base_pay >= INTERN_SALARY_RANGE_INDIA[0] and base_pay <= INTERN_SALARY_RANGE_INDIA[1]


def _is_valid_yearly_total_pay_from_india(base_pay: float):
    return base_pay >= TOTAL_SALARY_RANGE_INDIA[0] and base_pay <= TOTAL_SALARY_RANGE_INDIA[1]


def _filter_invalid_salaries(raw_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    n_india = 0
    n_dropped = 0
    filtered_info = []
    for r in raw_info:
        if "country" in r and r["country"] == "india":
            n_india += 1
            if "cleanSalaryTotal" in r and r["cleanSalaryTotal"] != -1:
                if not _is_valid_yearly_total_pay_from_india(r["cleanSalaryTotal"]):
                    r["cleanSalaryTotal"] = -1.0
                elif r["cleanSalaryTotal"] / r["cleanSalary"] > TOTAL_TO_BASE_MAX_RATIO:
                    r["cleanSalaryTotal"] = -1.0
            if r["yrOrPm"] == "yearly" and not _is_valid_yearly_base_pay_from_india(r["cleanSalary"]):
                n_dropped += 1
                continue
            elif r["yrOrPm"] == "monthly" and not _is_valid_monthly_internship_pay_from_india(r["cleanSalary"]):
                n_dropped += 1
                continue
            else:
                filtered_info.append(r)
    logger.info(f"Dropped {n_dropped}/{n_india} records due to invalid pay")
    return filtered_info


def _add_clean_yoe_and_salaries(expanded_info: List[Dict[str, Any]], info: Dict[str, Any], title: str) -> None:
    for info in expanded_info:
        info["cleanYoe"] = _get_clean_yoe(info["yoe"].lower(), _preprocess_text(title).lower(), info["role"].lower())
        if "country" in info and info["country"] == "india":
            if "\\n" in info["salary"].replace(",", "").lower():
                info["cleanSalary"], info["yrOrPm"] = _get_clean_salary_for_india(
                    info["salary"].replace(",", "").lower().split("\\n")[0]
                )
            else:
                info["cleanSalary"], info["yrOrPm"] = _get_clean_salary_for_india(
                    info["salary"].replace(",", "").lower()
                )
            if info["yrOrPm"] == "yearly":
                total_salary, _ = _get_clean_salary_for_india(
                    info["salaryTotal"].replace(",", "").lower().split("\\n")[0]
                )
                if info["cleanSalary"] != -1 and total_salary > info["cleanSalary"]:
                    info["cleanSalaryTotal"] = total_salary
                else:
                    info["cleanSalaryTotal"] = -1


def _get_clean_company_text(company: str) -> str:
    return " ".join(re.findall(r"\w+", company.lower()))


def _add_clean_companies(raw_info: List[Dict[str, Any]]) -> None:
    companies_counter = Counter([_get_clean_company_text(r["company"]) for r in raw_info])
    clean_company_map = {}
    for company, _ in companies_counter.most_common(len(companies_counter)):
        if company.split(" ")[0] not in clean_company_map:
            clean_company_map[company] = company
        else:
            clean_company_map[company] = company.split(" ")[0]
    for r in raw_info:
        clean_company = clean_company_map[_get_clean_company_text(r["company"])]
        r["cleanCompany"] = " ".join([txt.capitalize() for txt in clean_company.split(" ")])


def _drop_info(raw_info: List[Dict[str, Any]]) -> None:
    for r in raw_info:
        try:
            del r["title"], r["yoe"], r["salary"], r["salaryTotal"], r["city"], r["country"]
        except KeyError:
            continue


def _save_raw_info(raw_info: List[Dict[str, Any]]) -> None:
    with open("data/posts_info.json", "w") as f:
        json.dump(raw_info, f)


def _save_meta_info(total_posts: int, raw_info: List[Dict[str, Any]]) -> Dict[str, Any]:
    # top 20 companies
    company_counter = Counter([r["cleanCompany"] for r in raw_info])
    top_20 = [(company, count) for company, count in company_counter.most_common(20)]
    # most offers in the last 1 month
    from_date = (datetime.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    filtered_info = [r for r in raw_info if r["date"] >= from_date]
    company_counter = Counter([r["cleanCompany"] for r in filtered_info])
    most_offers = [(company, count) for company, count in company_counter.most_common(10)]
    # meta data
    meta_info = {
        "totalPosts": total_posts,
        "totalPostsFromIndia": len([r for r in raw_info if "country" in r and r["country"] == "india"]),
        "totalPostsWithTotalComp": len(
            [r for r in raw_info if "cleanSalaryTotal" in r and r["cleanSalaryTotal"] != -1.0]
        ),
        "lastUpdated": datetime.now().strftime("%Y/%m/%d %H:%M:%S"),
        "top20Companies": top_20,
        "mostOffersInLastMonth": most_offers,
    }
    with open("data/meta_info.json", "w") as f:
        json.dump(meta_info, f)

    return meta_info


def _update_data_in_js(raw_info: List[Dict[str, Any]], meta_info: Dict[str, Any]) -> None:
    with open("js/data.js", "w") as f:
        f.write(f"var metaInfo = {json.dumps(meta_info)};\n\n")
        # we are only saving the values here to reduce data size and save network cost
        # when client loads the static content
        f.write(f"var allData = {json.dumps([list(r.values()) for r in raw_info])};")


def parse_posts_and_save_tagged_info() -> None:
    # fmt: off
    raw_info = []; n_dropped = 0; total_posts = 0; content = {}
    with session_scope() as session:
        for r in session.query(Posts).all():
            total_posts += 1
            info = {"id": r.id, "title": r.title, "voteCount": r.voteCount, "viewCount": r.viewCount,
                    "date": datetime.fromtimestamp(int(r.creationDate)).strftime("%Y-%m-%d")}
            clean_content = _preprocess_text(r.content)
            content[r.id] = clean_content
            companies = _find_matches(LABEL_SPECIFICATION["RE_COMPANY"], clean_content)
            roles = _find_matches(LABEL_SPECIFICATION["RE_ROLE"], clean_content)
            yoes = _find_matches(LABEL_SPECIFICATION["RE_YOE"], clean_content)
            salaries = _find_matches(LABEL_SPECIFICATION["RE_SALARY"], clean_content)
            total_salaies = _find_matches(LABEL_SPECIFICATION["RE_SALARY_TOTAL"], clean_content)
            if companies and roles and yoes and salaries:
                expanded_info = _get_info_as_flat_list(companies, roles, yoes, salaries, total_salaies, info)
                location = _get_clean_location(_preprocess_text(r.title), clean_content)
                if location[1]:
                    for info in expanded_info:
                        info["city"] = location[0]; info["country"] = location[1]
                _add_clean_yoe_and_salaries(expanded_info, info, r.title)
                raw_info += expanded_info
            else:
                n_dropped += 1
    # fmt: on
    logger.info(f"Total posts: {total_posts}")
    logger.info(f"N posts dropped (missing data): {n_dropped}")
    _report(raw_info)
    raw_info = _filter_invalid_salaries(raw_info)

    _add_clean_companies(raw_info)
    raw_info = sorted(raw_info, key=lambda x: x["date"], reverse=True)
    meta_info = _save_meta_info(total_posts, raw_info)
    _drop_info(raw_info)
    _save_raw_info(raw_info)
    _update_data_in_js(raw_info, meta_info)


if __name__ == "__main__":
    parse_posts_and_save_tagged_info()
