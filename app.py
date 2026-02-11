"""
Vet Clin Path MCQ Generator
Extracts articles from veterinary clinical pathology journals and generates MCQs
"""

import os
import random
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, jsonify, request
import requests
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key')

PUBMED_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# Journal configurations with PubMed search terms
JOURNALS = {
    "VCP": {
        "name": "Veterinary Clinical Pathology",
        "abbrev": "Vet Clin Pathol",
        "query": '"Vet Clin Pathol"[Journal]',
        "exclude": None
    }
}


def get_date_range(months=12):
    """Get date range for the specified number of months"""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=months * 30)
    return start_date.strftime("%Y/%m/%d"), end_date.strftime("%Y/%m/%d")


def get_article_count(months=12, journal="VCP"):
    """Get total article count from PubMed (no limit) for a specific journal"""
    start_date, end_date = get_date_range(months)

    j_info = JOURNALS.get(journal, JOURNALS["VCP"])
    journal_query = j_info['query']
    if j_info['exclude']:
        journal_query = f"({journal_query} NOT {j_info['exclude']}[Publication Type])"

    query = f'{journal_query} AND ("{start_date}"[Date - Publication] : "{end_date}"[Date - Publication])'

    search_url = f"{PUBMED_BASE_URL}/esearch.fcgi"
    search_params = {
        "db": "pubmed",
        "term": query,
        "retmax": 0,  # Don't need IDs, just the count
        "retmode": "json"
    }

    try:
        response = requests.get(search_url, params=search_params, timeout=30)
        response.raise_for_status()
        search_results = response.json()
        return int(search_results.get("esearchresult", {}).get("count", 0))
    except requests.RequestException as e:
        print(f"Error getting article count: {e}")
        return 0


def search_pubmed_articles(months=12, journal="all"):
    """Search PubMed for articles from specified journal(s)"""
    start_date, end_date = get_date_range(months)

    # Build search query based on journal selection
    if journal == "all":
        # Search all journals
        journal_queries = []
        for j_key, j_info in JOURNALS.items():
            jq = j_info['query']
            if j_info['exclude']:
                jq = f"({jq} NOT {j_info['exclude']}[Publication Type])"
            journal_queries.append(jq)
        journal_query = "(" + " OR ".join(journal_queries) + ")"
    else:
        # Search specific journal
        j_info = JOURNALS.get(journal, JOURNALS["VCP"])
        journal_query = j_info['query']
        if j_info['exclude']:
            journal_query = f"({journal_query} NOT {j_info['exclude']}[Publication Type])"

    query = f'{journal_query} AND ("{start_date}"[Date - Publication] : "{end_date}"[Date - Publication])'

    # First, search for article IDs
    search_url = f"{PUBMED_BASE_URL}/esearch.fcgi"
    search_params = {
        "db": "pubmed",
        "term": query,
        "retmax": 500,
        "retmode": "json",
        "sort": "pub_date"
    }

    try:
        response = requests.get(search_url, params=search_params, timeout=30)
        response.raise_for_status()
        search_results = response.json()

        id_list = search_results.get("esearchresult", {}).get("idlist", [])

        if not id_list:
            return []

        # Fetch article details
        return fetch_article_details(id_list)

    except requests.RequestException as e:
        print(f"Error searching PubMed: {e}")
        return []


def fetch_article_details(pmid_list):
    """Fetch detailed information for articles by PMID"""
    if not pmid_list:
        return []

    fetch_url = f"{PUBMED_BASE_URL}/efetch.fcgi"
    fetch_params = {
        "db": "pubmed",
        "id": ",".join(pmid_list),
        "retmode": "xml",
        "rettype": "abstract"
    }

    try:
        response = requests.get(fetch_url, params=fetch_params, timeout=30)
        response.raise_for_status()

        # Parse XML response
        from xml.etree import ElementTree as ET
        root = ET.fromstring(response.content)

        articles = []
        for article in root.findall(".//PubmedArticle"):
            article_data = parse_article_xml(article)
            if article_data:
                articles.append(article_data)

        return articles

    except requests.RequestException as e:
        print(f"Error fetching article details: {e}")
        return []


def parse_article_xml(article_element):
    """Parse individual article XML element"""
    try:
        medline = article_element.find(".//MedlineCitation")
        if medline is None:
            return None

        pmid_elem = medline.find(".//PMID")
        pmid = pmid_elem.text if pmid_elem is not None else "Unknown"

        article = medline.find(".//Article")
        if article is None:
            return None

        # Title - use itertext() to capture text within nested tags (italics, etc.)
        title_elem = article.find(".//ArticleTitle")
        if title_elem is not None:
            title = "".join(title_elem.itertext())
        else:
            title = "No title"

        # Abstract - use itertext() to capture text within nested tags (italics, etc.)
        abstract_parts = article.findall(".//Abstract/AbstractText")
        if abstract_parts:
            abstract_texts = []
            for part in abstract_parts:
                part_text = "".join(part.itertext())
                if part_text:
                    # Add label if present (for structured abstracts)
                    label = part.get("Label", "")
                    if label:
                        abstract_texts.append(f"{label}: {part_text}")
                    else:
                        abstract_texts.append(part_text)
            abstract = " ".join(abstract_texts) if abstract_texts else "No abstract available"
        else:
            abstract = "No abstract available"

        # Authors
        authors = []
        for author in article.findall(".//AuthorList/Author"):
            last_name = author.find("LastName")
            fore_name = author.find("ForeName")
            if last_name is not None:
                name = last_name.text
                if fore_name is not None:
                    name = f"{fore_name.text} {name}"
                authors.append(name)

        # Publication date
        pub_date = article.find(".//Journal/JournalIssue/PubDate")
        date_str = ""
        if pub_date is not None:
            year = pub_date.find("Year")
            month = pub_date.find("Month")
            if year is not None:
                date_str = year.text
                if month is not None:
                    date_str = f"{month.text} {date_str}"

        # Journal info
        journal = article.find(".//Journal/Title")
        journal_name = journal.text if journal is not None else JOURNAL_NAME

        return {
            "pmid": pmid,
            "title": title,
            "abstract": abstract,
            "authors": ", ".join(authors[:5]) + ("..." if len(authors) > 5 else ""),
            "pub_date": date_str,
            "journal": journal_name,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        }

    except Exception as e:
        print(f"Error parsing article: {e}")
        return None


def generate_mcq_from_article(article, num_questions=1):
    """Generate veterinary clinical pathology MCQ from an article using Claude API"""
    api_key = os.getenv('ANTHROPIC_API_KEY')

    if not api_key:
        return generate_fallback_mcq(article, num_questions, "ANTHROPIC_API_KEY environment variable is not set")

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""Based on the following veterinary clinical pathology article abstract, generate {num_questions} multiple choice question(s) in board examination style.

Article Title: {article['title']}

Abstract: {article['abstract']}

For each question:
1. Create a clinically relevant question that tests understanding of the key findings or concepts
2. Provide 5 answer options (A, B, C, D, E) with 1 correct answer and 4 distractors
3. Indicate the correct answer
4. Provide a brief explanation

Format each question as:
QUESTION [number]:
[Question text]

A) [Option A]
B) [Option B]
C) [Option C]
D) [Option D]
E) [Option E]

CORRECT ANSWER: [Letter]

EXPLANATION: [Brief explanation of why this is correct and why other options are incorrect]

---

Make questions appropriate for board-level veterinary clinical pathologists."""

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        return {
            "article_title": article['title'],
            "article_pmid": article['pmid'],
            "article_url": article['url'],
            "article_authors": article['authors'],
            "article_journal": article['journal'],
            "article_year": article['pub_date'],
            "questions": message.content[0].text
        }

    except Exception as e:
        print(f"Error generating MCQ with API: {e}")
        return generate_fallback_mcq(article, num_questions, str(e))


def generate_fallback_mcq(article, num_questions=1, error_msg=None):
    """Generate a basic template MCQ when API is unavailable"""
    error_note = f"Error: {error_msg}" if error_msg else "Note: API key not configured. Please add your ANTHROPIC_API_KEY to generate AI-powered questions."
    return {
        "article_title": article['title'],
        "article_pmid": article['pmid'],
        "article_url": article['url'],
        "article_authors": article['authors'],
        "article_journal": article['journal'],
        "article_year": article['pub_date'],
        "questions": f"""QUESTION 1:
Based on the study "{article['title']}", which of the following statements is most accurate regarding the findings?

A) [Review the abstract to determine the correct answer]
B) [Alternative interpretation]
C) [Common misconception]
D) [Unrelated finding]
E) [Another distractor]

CORRECT ANSWER: [To be determined after reviewing full article]

EXPLANATION: Please review the full article at {article['url']} to determine the correct answer and explanation.

---
{error_note}"""
    }


# Routes
@app.route('/')
def index():
    """Main page"""
    return render_template('index.html', journals=JOURNALS)


@app.route('/api/articles')
def get_articles():
    """API endpoint to fetch articles"""
    months = int(request.args.get('months', 12))
    journal = request.args.get('journal', 'all')
    articles = search_pubmed_articles(months, journal)
    return jsonify({
        "success": True,
        "count": len(articles),
        "articles": articles,
        "date_range": get_date_range(months),
        "journal": journal
    })


@app.route('/api/journal-stats')
def get_journal_stats():
    """API endpoint to get article counts by journal for chart (no limit)"""
    months = int(request.args.get('months', 12))

    stats = {}
    for j_key, j_info in JOURNALS.items():
        count = get_article_count(months, j_key)
        stats[j_key] = {
            "name": j_info['name'],
            "abbrev": j_info['abbrev'],
            "count": count
        }

    return jsonify({
        "success": True,
        "stats": stats,
        "months": months
    })


@app.route('/api/generate-mcq', methods=['POST'])
def generate_mcq():
    """API endpoint to generate MCQs"""
    data = request.json
    num_questions = int(data.get('num_questions', 5))
    months = int(data.get('months', 12))
    journal = data.get('journal', 'all')

    # Fetch articles using the selected time period and journal
    articles = search_pubmed_articles(months, journal)

    if not articles:
        return jsonify({
            "success": False,
            "error": f"No articles found in the selected time period ({months} months)"
        })

    # Filter articles with abstracts
    articles_with_abstracts = [a for a in articles if a['abstract'] != "No abstract available"]

    if not articles_with_abstracts:
        articles_with_abstracts = articles

    # Randomly select articles for questions
    num_articles = min(num_questions, len(articles_with_abstracts))
    selected_articles = random.sample(articles_with_abstracts, num_articles)

    # Generate MCQs in parallel for faster processing
    mcq_results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(generate_mcq_from_article, article, 1): article for article in selected_articles}
        for future in as_completed(futures):
            try:
                mcq = future.result(timeout=60)
                mcq_results.append(mcq)
            except Exception as e:
                article = futures[future]
                mcq_results.append({
                    "article_title": article['title'],
                    "article_pmid": article['pmid'],
                    "article_url": article['url'],
                    "article_authors": article['authors'],
                    "article_journal": article['journal'],
                    "article_year": article['pub_date'],
                    "questions": f"Error generating question: {str(e)}"
                })

    return jsonify({
        "success": True,
        "mcq_results": mcq_results
    })


if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(debug=debug_mode, host='0.0.0.0', port=5000)
