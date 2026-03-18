# -*- coding: utf-8 -*-
"""
LinkedIn Job Scraper — Parallel Version
Fetches job details 5 at a time simultaneously
~5x faster than sequential version
"""

import asyncio, json, nest_asyncio, re, sys
nest_asyncio.apply()
from playwright.async_api import async_playwright

BASE_URL = "https://www.linkedin.com/jobs/search/?f_TPR=r600&geoId=102713980&keywords=java&sortBy=DD"
CONCURRENCY = 5   # fetch 5 jobs at the same time — increase to 8 if stable

def clean_location(raw):
    if not raw:
        return None
    skip_patterns = [
        r'\d+\s+(minute|hour|day|week)s?\s+ago',
        r'actively hiring', r'be an early applicant',
        r'promoted', r'easy apply', r'viewed', r'applied',
    ]
    clean = []
    for line in raw.split("\n"):
        line = line.strip()
        if line and not any(re.search(p, line, re.IGNORECASE) for p in skip_patterns):
            clean.append(line)
    return ", ".join(clean) if clean else None

def clean_time_ago(raw):
    if not raw:
        return None
    match = re.search(r'(\d+\s+(?:minute|hour|day|week)s?\s+ago)', raw, re.IGNORECASE)
    return match.group(1) if match else None

async def dismiss_popup(page):
    for sel in [
        '[data-tracking-control-name="public_jobs_contextual-sign-in-modal_modal_dismiss"]',
        'button[aria-label="Dismiss"]',
        'button[aria-label="Close"]',
        '[class*="dismiss"]',
    ]:
        try:
            btn = await page.wait_for_selector(sel, timeout=1500)
            if btn:
                await btn.click()
                await page.wait_for_timeout(800)
                return True
        except:
            continue
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)
    except:
        pass
    return False

CHECK_END_JS = """
    () => {
        var allText = document.body.innerText;
        var endPhrases = [
            "You've viewed all jobs for this search",
            "you've viewed all jobs",
            "No more jobs available",
            "no matching jobs"
        ];
        for (var phrase of endPhrases) {
            if (allText.toLowerCase().includes(phrase.toLowerCase())) return phrase;
        }
        return null;
    }
"""

EXTRACT_JS = """
    () => {
        var results = [];
        var seen    = new Set();
        var links   = document.querySelectorAll('a[href*="/jobs/view/"]');
        for (var i = 0; i < links.length; i++) {
            var link = links[i];
            var url  = link.href.split('?')[0];
            if (seen.has(url)) continue;
            seen.add(url);
            var card = link.closest('li') ||
                       link.closest('[data-job-id]') ||
                       link.closest('[class*="result"]') ||
                       link.parentElement;
            var title = null, company = null, location = null,
                posted = null, easyApply = false;
            if (card) {
                var t = card.querySelector('h3, strong, [class*="title"]');
                if (t) title = t.innerText.trim();
                var c = card.querySelector('h4, [class*="company"], [class*="subtitle"]');
                if (c) company = c.innerText.trim();
                var l = card.querySelector('[class*="location"], [class*="metadata"]');
                if (l) location = l.innerText.trim();
                var tm = card.querySelector('time');
                if (tm) posted = tm.getAttribute('datetime') || tm.innerText.trim();
                easyApply = !!card.querySelector('[class*="easy-apply"]');
            }
            if (!title) title = link.innerText.trim();
            if (title && title.length > 1)
                results.push({ title, company, location, posted, easyApply, url });
        }
        return results;
    }
"""

DETAIL_JS = """
    () => {
        var d = {
            applicants: null, apply_link: null, apply_type: null,
            easy_apply: false, about_job: null, seniority_level: null,
            employment_type: null, job_function: null, industries: null,
            hiring_team: []
        };

        var appEl = document.querySelector('[class*="num-applicants"], [class*="applicant-count"], [class*="applies"]');
        if (appEl) d.applicants = appEl.innerText.trim();

        var applyBtn = document.querySelector('a[class*="apply-button"][href], a[data-tracking-control-name*="apply"]');
        if (applyBtn) { d.apply_link = applyBtn.href; d.apply_type = applyBtn.innerText.trim(); }
        d.easy_apply = !!document.querySelector('[class*="easy-apply"], button[aria-label*="Easy Apply"]');

        var descEl = document.querySelector('.show-more-less-html__markup, [class*="description__text"], [class*="job-description"], .jobs-description-content__text');
        if (descEl) d.about_job = descEl.innerText.trim();

        var criteriaItems = document.querySelectorAll('.description__job-criteria-item, [class*="job-criteria-item"]');
        for (var item of criteriaItems) {
            var label = item.querySelector('[class*="subheader"], h3');
            var value = item.querySelector('[class*="criteria-text"], span');
            if (label && value) {
                var l = label.innerText.trim().toLowerCase();
                var v = value.innerText.trim();
                if (l.includes('seniority'))  d.seniority_level = v;
                if (l.includes('employment')) d.employment_type = v;
                if (l.includes('function'))   d.job_function    = v;
                if (l.includes('industr'))    d.industries      = v;
            }
        }

        var hiringSelectors = [
            '.hirer-card__hirer-information', '[class*="hirer-card"]',
            '[class*="hiring-team"]', '[class*="message-the-recruiter"]',
            '.jobs-poster__name', '[class*="recruiter"]',
        ];
        for (var sel of hiringSelectors) {
            var cards = document.querySelectorAll(sel);
            for (var card of cards) {
                var nameEl    = card.querySelector('[class*="name"], strong, h3, h4');
                var roleEl    = card.querySelector('[class*="title"], [class*="subtitle"], p');
                var profileEl = card.querySelector('a[href*="/in/"]');
                var name = nameEl ? nameEl.innerText.trim() : null;
                if (!name || name.length < 2) continue;
                var already = d.hiring_team.some(function(p) { return p.name === name; });
                if (already) continue;
                d.hiring_team.push({
                    name:    name,
                    role:    roleEl    ? roleEl.innerText.trim()      : null,
                    profile: profileEl ? profileEl.href.split('?')[0] : null,
                });
            }
            if (d.hiring_team.length > 0) break;
        }
        return d;
    }
"""

async def scroll_job_list(page):
    try:
        job_card = await page.query_selector('a[href*="/jobs/view/"]')
        if job_card:
            box = await job_card.bounding_box()
            if box:
                await page.mouse.move(box["x"] - 50, box["y"])
                await page.mouse.wheel(0, 3000)
                await page.wait_for_timeout(1500)
                await page.mouse.wheel(0, 3000)
                await page.wait_for_timeout(1500)
                return
    except:
        pass
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")


# ── Scrape all job cards from search results ──────────────────────────────────
async def scrape_job_list(browser):
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 900},
        locale="en-US",
    )
    page = await context.new_page()
    await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000)
    await dismiss_popup(page)

    all_jobs     = []
    round_num    = 0
    no_new_count = 0

    while round_num < 60:
        round_num += 1
        try:
            btn = page.locator(
                'button.infinite-scroller__show-more-button, '
                'button[aria-label*="See more jobs"], '
                '[class*="see-more-jobs"]'
            ).first
            if await btn.is_visible(timeout=1000):
                await btn.click()
                await page.wait_for_timeout(2500)
        except:
            pass

        await scroll_job_list(page)
        await page.wait_for_timeout(2000)
        await dismiss_popup(page)

        try:
            page_jobs = await page.evaluate(EXTRACT_JS)
        except:
            page_jobs = []

        existing = {j["url"] for j in all_jobs}
        new_jobs = [j for j in page_jobs if j["url"] not in existing]
        all_jobs.extend(new_jobs)

        end_msg = await page.evaluate(CHECK_END_JS)
        if end_msg:
            break

        if len(new_jobs) == 0:
            no_new_count += 1
            if no_new_count >= 3:
                break
        else:
            no_new_count = 0

        await asyncio.sleep(1)

    await context.close()
    return all_jobs


# ── Fetch details for a single job using its own browser context ──────────────
async def fetch_job_detail(browser, job, semaphore):
    async with semaphore:   # limits to CONCURRENCY simultaneous fetches
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = await context.new_page()
        try:
            await page.goto(job["url"], wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1500)
            await dismiss_popup(page)

            for pos in [400, 800, 1200, 1600]:
                await page.evaluate(f"window.scrollTo(0, {pos})")
                await page.wait_for_timeout(200)
            await page.wait_for_timeout(800)

            details = await page.evaluate(DETAIL_JS)

            if not details.get("apply_link"):
                details["apply_link"] = job["url"]

            # Flatten hiring team
            hiring_str = ""
            if details.get("hiring_team"):
                parts = []
                for p in details["hiring_team"]:
                    line = p.get("name", "")
                    if p.get("role"):    line += f" ({p['role']})"
                    if p.get("profile"): line += f" → {p['profile']}"
                    parts.append(line)
                hiring_str = " | ".join(parts)

            return {
                **job,
                "about_job":       details.get("about_job"),
                "apply_link":      details.get("apply_link") or job["url"],
                "apply_type":      details.get("apply_type"),
                "easy_apply":      details.get("easy_apply", False),
                "seniority_level": details.get("seniority_level"),
                "employment_type": details.get("employment_type"),
                "job_function":    details.get("job_function"),
                "industries":      details.get("industries"),
                "applicants":      details.get("applicants"),
                "hiring_team":     hiring_str,
            }

        except Exception as e:
            print(f"  ⚠️  Failed {job.get('title','?')} @ {job.get('company','?')}: {e}", file=sys.stderr)
            return {
                **job,
                "about_job": None, "apply_link": job["url"],
                "apply_type": None, "easy_apply": False,
                "seniority_level": None, "employment_type": None,
                "job_function": None, "industries": None,
                "applicants": None, "hiring_team": "",
            }
        finally:
            await context.close()


# ── Fetch ALL job details in parallel ─────────────────────────────────────────
async def fetch_all_details_parallel(browser, jobs):
    semaphore = asyncio.Semaphore(CONCURRENCY)
    tasks     = [fetch_job_detail(browser, job, semaphore) for job in jobs]

    print(f"Fetching details for {len(jobs)} jobs ({CONCURRENCY} at a time)...", file=sys.stderr)

    results   = []
    completed = 0

    for coro in asyncio.as_completed(tasks):
        result = await coro
        completed += 1
        print(f"  [{completed}/{len(jobs)}] ✅ {result.get('title','?')} @ {result.get('company','?')}", file=sys.stderr)
        results.append(result)

    return results


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"]
        )

        # Step 1 — Scrape job list (sequential — one page)
        print("Scraping job list...", file=sys.stderr)
        all_jobs = await scrape_job_list(browser)
        print(f"Found {len(all_jobs)} jobs", file=sys.stderr)

        # Step 2 — Fetch details in parallel
        all_jobs = await fetch_all_details_parallel(browser, all_jobs)

        await browser.close()

    # Step 3 — Clean and output
    output = []
    for job in all_jobs:
        if not job.get("about_job"):
            continue
        raw = job.get("location", "")
        output.append({
            "title":       job.get("title")        or "",
            "company":     job.get("company")      or "",
            "location":    clean_location(raw)     or "",
            "url":         job.get("url")          or "",
            "apply_link":  job.get("apply_link")   or job.get("url", ""),
            "hiring_team": job.get("hiring_team")  or "",
            "about_job":   job.get("about_job")    or "",   # needed for AI fit check
        })

    print(json.dumps(output))


asyncio.get_event_loop().run_until_complete(main())
