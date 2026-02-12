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


# eClinPath topic tree (Cornell University veterinary clinical pathology resource)
ECLINPATH_TOPICS = {
    "Hematology": {
        "url": "https://eclinpath.com/hematology/",
        "subtopics": {
            "Hemogram basics": "https://eclinpath.com/hematology/hemogram-basics/",
            "Blood smear examination": "https://eclinpath.com/hematology/hemogram-basics/blood-smear-examination/",
            "Leukogram patterns": "https://eclinpath.com/hematology/hemogram-basics/leukogram/",
            "RBC morphology": "https://eclinpath.com/hematology/morphologic-features/red-blood-cells/",
            "Normal erythrocytes": "https://eclinpath.com/hematology/morphologic-features/red-blood-cells/normal-erythrocytes/",
            "WBC morphology & leukocytes": "https://eclinpath.com/hematology/leukogram-changes/leukocytes/",
            "Platelet morphology": "https://eclinpath.com/hematology/morphologic-features/platelets/",
            "Reticulocyte count": "https://eclinpath.com/hematology/tests/absolute-reticulocyte-count/",
            "Reticulocyte indices": "https://eclinpath.com/hematology/tests/reticulocyte-indices/",
            "Erythrocytosis / Polycythemia": "https://eclinpath.com/hematology/polycythemia/",
            "WBC counts": "https://eclinpath.com/hematology/tests/wbc-count/",
            "Hematology quick guide": "https://eclinpath.com/hematology/tests/hematology-guide/",
        }
    },
    "Chemistry - Liver": {
        "url": "https://eclinpath.com/chemistry/liver/",
        "subtopics": {
            "ALT (Alanine aminotransferase)": "https://eclinpath.com/chemistry/liver/liver-injury/alanine-aminotransferase/",
            "AST (Aspartate aminotransferase)": "https://eclinpath.com/chemistry/liver/liver-injury/aspartate-aminotransferase/",
            "ALP (Alkaline phosphatase)": "https://eclinpath.com/chemistry/liver/cholestasis/alkaline-phosphatase/",
            "GGT (Gamma-glutamyl transferase)": "https://eclinpath.com/chemistry/liver/cholestasis/gamma-glutamyl-transferase/",
            "Bilirubin": "https://eclinpath.com/chemistry/liver/cholestasis/bilirubin/",
            "Cholestasis": "https://eclinpath.com/chemistry/liver/cholestasis/",
            "Liver function tests": "https://eclinpath.com/chemistry/liver/liver-function-tests/",
            "Laboratory detection of liver disease": "https://eclinpath.com/chemistry/liver/laboratory-detection/",
        }
    },
    "Chemistry - Kidney": {
        "url": "https://eclinpath.com/chemistry/kidney/",
        "subtopics": {
            "Urea nitrogen (BUN)": "https://eclinpath.com/chemistry/kidney/urea-nitrogen/",
            "Creatinine": "https://eclinpath.com/chemistry/kidney/creatinine/",
            "SDMA": "https://eclinpath.com/chemistry/kidney/sdma/",
            "GFR (Glomerular filtration rate)": "https://eclinpath.com/chemistry/kidney/gfr/",
            "Azotemia": "https://eclinpath.com/chemistry/kidney/azotemia/",
            "Types of renal disease": "https://eclinpath.com/chemistry/kidney/types-of-renal-disease/",
            "Renal physiology": "https://eclinpath.com/chemistry/kidney/physiology/",
        }
    },
    "Chemistry - Electrolytes & Acid-Base": {
        "url": "https://eclinpath.com/chemistry/electrolytes/",
        "subtopics": {
            "Electrolytes overview": "https://eclinpath.com/chemistry/electrolytes/",
            "Potassium": "https://eclinpath.com/chemistry/electrolytes/potassium/",
            "Acid-base": "https://eclinpath.com/chemistry/acid-base/",
            "Bicarbonate": "https://eclinpath.com/chemistry/acid-base/chemistry-tests/bicarbonate/",
        }
    },
    "Chemistry - Minerals": {
        "url": "https://eclinpath.com/chemistry/minerals/overview/",
        "subtopics": {
            "Minerals overview (Ca, P, Mg)": "https://eclinpath.com/chemistry/minerals/overview/",
            "Calcium (total)": "https://eclinpath.com/chemistry/minerals/calcium/",
            "Free ionized calcium": "https://eclinpath.com/chemistry/minerals/ionized-calcium/",
            "Phosphate": "https://eclinpath.com/chemistry/minerals/phosphate/",
        }
    },
    "Chemistry - Proteins": {
        "url": "https://eclinpath.com/chemistry/proteins/",
        "subtopics": {
            "Proteins overview": "https://eclinpath.com/chemistry/proteins/",
            "Total protein": "https://eclinpath.com/chemistry/proteins/total-protein/",
        }
    },
    "Chemistry - Energy & Metabolites": {
        "url": "https://eclinpath.com/chemistry/energy-metabolism/",
        "subtopics": {
            "Energy metabolism overview": "https://eclinpath.com/chemistry/energy-metabolism/",
            "Glucose": "https://eclinpath.com/chemistry/energy-metabolism/glucose/",
            "Cholesterol": "https://eclinpath.com/chemistry/energy-metabolism/cholesterol/",
        }
    },
    "Chemistry - Iron Metabolism": {
        "url": "https://eclinpath.com/chemistry/iron-metabolism/",
        "subtopics": {
            "Iron metabolism overview": "https://eclinpath.com/chemistry/iron-metabolism/",
            "Iron physiology": "https://eclinpath.com/chemistry/iron-metabolism/physiology/",
            "Iron distribution": "https://eclinpath.com/chemistry/iron-metabolism/iron-2/",
            "Heme metabolism": "https://eclinpath.com/chemistry/iron-metabolism/heme-metabolism/",
        }
    },
    "Hemostasis": {
        "url": "https://eclinpath.com/hemostasis/",
        "subtopics": {
            "Hemostasis physiology": "https://eclinpath.com/hemostasis/physiology/",
            "Primary hemostasis": "https://eclinpath.com/hemostasis/physiology/primary-hemostasis/",
            "Secondary hemostasis": "https://eclinpath.com/hemostasis/physiology/secondary-hemostasis/",
            "Coagulation cascade": "https://eclinpath.com/hemostasis/physiology/secondary-hemostasis/coagulation-cascade-new-model-3/",
            "Fibrinolysis": "https://eclinpath.com/hemostasis/physiology/fibrinolysis/",
            "Hemostasis tests overview": "https://eclinpath.com/hemostasis/tests/",
            "Screening coagulation assays (PT, APTT)": "https://eclinpath.com/hemostasis/tests/screening-coagulation-assays/",
            "DIC": "https://eclinpath.com/hemostasis/disorders/dic/",
        }
    },
    "Urinalysis": {
        "url": "https://eclinpath.com/urinalysis/",
        "subtopics": {
            "Urinalysis overview": "https://eclinpath.com/urinalysis/",
            "Chemical constituents": "https://eclinpath.com/urinalysis/chemical-constituents/",
            "Cellular constituents": "https://eclinpath.com/urinalysis/cellular-constituents/",
            "Casts": "https://eclinpath.com/urinalysis/casts/",
            "Crystals": "https://eclinpath.com/urinalysis/crystals/",
            "Crystal quick guide": "https://eclinpath.com/urinalysis/crystal-quick-guide/",
            "Cell quick guide": "https://eclinpath.com/urinalysis/cell-quick-quide/",
        }
    },
    "Test Basics": {
        "url": "https://eclinpath.com/test-basics/",
        "subtopics": {
            "Sample collection": "https://eclinpath.com/test-basics/sample-collection-2/",
            "Test interpretation": "https://eclinpath.com/test-basics/test-interpretation/",
            "Interferences": "https://eclinpath.com/test-basics/interferences/",
        }
    },
}


def generate_mcq_from_eclinpath(topic_name, subtopic_name, subtopic_url, num_questions=1):
    """Generate MCQ based on eClinPath veterinary clinical pathology topic"""
    api_key = os.getenv('ANTHROPIC_API_KEY')

    if not api_key:
        return {
            "article_title": f"{topic_name} - {subtopic_name}",
            "article_pmid": "",
            "article_url": subtopic_url,
            "article_authors": "eClinPath, Cornell University",
            "article_journal": "eClinPath",
            "article_year": "",
            "questions": "Error: ANTHROPIC_API_KEY environment variable is not set"
        }

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""You are an expert in veterinary clinical pathology. Generate {num_questions} board examination-style multiple choice question(s) on the following topic from eClinPath (Cornell University's veterinary clinical pathology resource).

Topic Category: {topic_name}
Specific Topic: {subtopic_name}
Reference URL: {subtopic_url}

Generate questions that test deep understanding of this topic as it relates to veterinary clinical pathology. Questions should be at the level expected for ACVP (American College of Veterinary Pathologists) board certification.

Include species-specific considerations where relevant (dogs, cats, horses, cattle, birds, reptiles).

For each question:
1. Create a clinically relevant question, ideally presenting a clinical scenario with laboratory findings
2. Provide 5 answer options (A, B, C, D, E) with 1 correct answer and 4 plausible distractors
3. Indicate the correct answer
4. Provide a detailed explanation referencing the underlying pathophysiology

Format each question as:
QUESTION [number]:
[Question text]

A) [Option A]
B) [Option B]
C) [Option C]
D) [Option D]
E) [Option E]

CORRECT ANSWER: [Letter]

EXPLANATION: [Detailed explanation of why this is correct and why other options are incorrect]

---"""

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        return {
            "article_title": f"{topic_name} - {subtopic_name}",
            "article_pmid": "",
            "article_url": subtopic_url,
            "article_authors": "eClinPath, Cornell University",
            "article_journal": "eClinPath",
            "article_year": "",
            "questions": message.content[0].text
        }

    except Exception as e:
        print(f"Error generating MCQ from eClinPath topic: {e}")
        return {
            "article_title": f"{topic_name} - {subtopic_name}",
            "article_pmid": "",
            "article_url": subtopic_url,
            "article_authors": "eClinPath, Cornell University",
            "article_journal": "eClinPath",
            "article_year": "",
            "questions": f"Error generating question: {str(e)}"
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


@app.route('/api/eclinpath-topics')
def get_eclinpath_topics():
    """API endpoint to get eClinPath topic tree"""
    return jsonify({
        "success": True,
        "topics": ECLINPATH_TOPICS
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


@app.route('/api/generate-mcq-eclinpath', methods=['POST'])
def generate_mcq_eclinpath():
    """API endpoint to generate MCQs from eClinPath topics"""
    data = request.json
    num_questions = int(data.get('num_questions', 5))
    selected_topics = data.get('topics', [])

    if not selected_topics:
        return jsonify({
            "success": False,
            "error": "No topics selected"
        })

    # Each item in selected_topics: {"category": "Hematology", "subtopic": "RBC morphology", "url": "..."}
    mcq_results = []
    questions_per_topic = max(1, num_questions // len(selected_topics))
    extra = num_questions - (questions_per_topic * len(selected_topics))

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}
        for i, topic in enumerate(selected_topics):
            q_count = questions_per_topic + (1 if i < extra else 0)
            if q_count <= 0:
                continue
            future = executor.submit(
                generate_mcq_from_eclinpath,
                topic['category'],
                topic['subtopic'],
                topic['url'],
                q_count
            )
            futures[future] = topic

        for future in as_completed(futures):
            try:
                mcq = future.result(timeout=120)
                mcq_results.append(mcq)
            except Exception as e:
                topic = futures[future]
                mcq_results.append({
                    "article_title": f"{topic['category']} - {topic['subtopic']}",
                    "article_pmid": "",
                    "article_url": topic['url'],
                    "article_authors": "eClinPath, Cornell University",
                    "article_journal": "eClinPath",
                    "article_year": "",
                    "questions": f"Error generating question: {str(e)}"
                })

    return jsonify({
        "success": True,
        "mcq_results": mcq_results
    })


if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(debug=debug_mode, host='0.0.0.0', port=5000)
