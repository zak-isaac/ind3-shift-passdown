import time
import ai_rewriter

text = ai_rewriter.build_input("TSP0002", [
    "retaped 1 of the threads on TSP0002",
    "I got the gasket from TSP0008. Upon installing the stud screws. I noticed two holes needed fixing. One hole is a little wider on one side making it so the screw not to stay in position and the other hole needs rethreading.",
    "Assist",
])
print(f"Prompt body length: {len(text)} chars")

asset_id = "TSP0002"
body = text.split(" / ", 1)[1]
context_block = ai_rewriter._asset_type_context(asset_id)
prompt = ai_rewriter._SINGLE_TEMPLATE.format(text=text, context_block=context_block)
print(f"Full prompt length: {len(prompt)} chars")
print(f"System prompt length: {len(ai_rewriter._SYSTEM_PROMPT)} chars")

t0 = time.perf_counter()
result = ai_rewriter._call_ollama(prompt, timeout=180, num_predict=200, model="gemma3n-e2b")
elapsed = time.perf_counter() - t0
print(f"\nElapsed: {elapsed:.1f}s")
print(f"Result: {result!r}")
