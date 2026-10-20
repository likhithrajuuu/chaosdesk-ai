import pytest

from chaosdesk.retrieval import retrieve_documents


@pytest.fixture
def kb_dir(tmp_path):
    (tmp_path / "password.md").write_text(
        "# Password reset\n\n"
        "Document ID: KB-001\n"
        "Reset a forgotten password using the reset email.\n",
        encoding="utf-8",
    )

    (tmp_path / "upload.md").write_text(
        "# Safari upload problem\n\n"
        "Document ID: KB-002\n"
        "The Safari upload button is unresponsive.\n"
        "Try Chrome as a workaround.\n",
        encoding="utf-8",
    )

    return tmp_path


def test_relevant_document_ranks_first(kb_dir):
    results = retrieve_documents(
        "Safari upload button unresponsive",
        kb_dir=kb_dir,
    )

    assert results[0].document_id == "KB-002"
    assert "Try Chrome as a workaround." in results[0].text


def test_unrelated_query_returns_no_results(kb_dir):
    results = retrieve_documents(
        "invoice refund",
        kb_dir=kb_dir,
    )

    assert results == []


def test_blank_query_returns_no_results(kb_dir):
    assert retrieve_documents("   ", kb_dir=kb_dir) == []


def test_limit_restricts_results(kb_dir):
    results = retrieve_documents(
        "password upload",
        kb_dir=kb_dir,
        limit=1,
    )

    assert len(results) == 1


def test_missing_directory_raises_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        retrieve_documents(
            "password",
            kb_dir=tmp_path / "missing",
        )


def test_missing_document_id_raises_error(tmp_path):
    (tmp_path / "invalid.md").write_text(
        "# Password reset\nNo document ID provided.",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="document ID"):
        retrieve_documents("password", kb_dir=tmp_path)