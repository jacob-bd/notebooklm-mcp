#!/usr/bin/env python3
"""Test script for delete_source method."""

import sys
from src.notebooklm_consumer_mcp.api_client import ConsumerNotebookLMClient
from src.notebooklm_consumer_mcp.auth import load_cached_tokens

def main():
    # Load credentials from cache
    tokens = load_cached_tokens()
    if not tokens:
        print("❌ No cached tokens found. Run save_auth_tokens first.")
        return 1

    # Initialize client
    client = ConsumerNotebookLMClient(
        cookies=tokens.cookies,
        csrf_token=tokens.csrf_token,
        session_id=tokens.session_id,
    )

    # Test notebook ID (Deep Research Import Test)
    notebook_id = "e683de8a-2a39-42fa-92bd-e59bfb07c870"

    # Get notebook details
    print(f"Getting notebook {notebook_id}...")
    result = client.get_notebook(notebook_id)

    if not result:
        print("❌ Failed to get notebook")
        return 1

    # Parse sources
    notebook_data = result[0] if isinstance(result[0], list) else result
    sources_data = notebook_data[1] if len(notebook_data) > 1 else []

    if not sources_data:
        print("❌ No sources found in notebook")
        return 1

    print(f"✅ Found {len(sources_data)} sources in notebook")

    # Pick the first source to delete
    first_source = sources_data[0]
    source_id = first_source[0][0] if first_source[0] and isinstance(first_source[0], list) else None
    title = first_source[1] if len(first_source) > 1 else "Untitled"

    if not source_id:
        print("❌ Could not extract source ID")
        return 1

    print(f"\nTarget source:")
    print(f"  ID: {source_id}")
    print(f"  Title: {title}")

    # Delete the source
    print(f"\n🗑️  Deleting source...")
    success = client.delete_source(source_id)

    if success:
        print("✅ Source deleted successfully!")

        # Verify deletion by getting notebook again
        print("\nVerifying deletion...")
        result_after = client.get_notebook(notebook_id)
        notebook_data_after = result_after[0] if isinstance(result_after[0], list) else result_after
        sources_data_after = notebook_data_after[1] if len(notebook_data_after) > 1 else []

        print(f"✅ Source count after deletion: {len(sources_data_after)} (was {len(sources_data)})")

        # Check that the deleted source is not in the list
        remaining_ids = [
            s[0][0] for s in sources_data_after
            if s[0] and isinstance(s[0], list) and len(s[0]) > 0
        ]

        if source_id not in remaining_ids:
            print(f"✅ Source {source_id} is no longer in the notebook")
        else:
            print(f"❌ Source {source_id} is still in the notebook!")
            return 1

        return 0
    else:
        print("❌ Failed to delete source")
        return 1

if __name__ == "__main__":
    sys.exit(main())
