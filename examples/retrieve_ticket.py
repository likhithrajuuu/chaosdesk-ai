from chaosdesk.schemas import Ticket
from chaosdesk.retrieval import retrieve_documents

def main():
    ticket = Ticket.model_validate({
        "ticket_id": "TKT-002",
        "subject" : "Safari upload button unresponsive",
        "description": "Clicking the upload button in Safari does nothing "
    })

    query = f"{ticket.subject} {ticket.description}"

    documents = retrieve_documents(query)

    print(f"Ticket: {ticket.ticket_id}")

    if not documents:
        print("No matching documents found")

    for document in documents:
        print(f"\nDocument: {document.document_id}")
        print(f"Retrieval Score: {document.score}")
        print(document.text)
if __name__ == "__main__":
    main()