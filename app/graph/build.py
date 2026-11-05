from langgraph.graph import StateGraph, START, END
from app.graph.state import ReviewState
from app.graph.nodes import guard, planner, retrieve, fan_out, reviewer, synthesizer

def build_graph():
    g = StateGraph(ReviewState)

    g.add_node("guard", guard)         # removes secrets before anything else sees the diff
    g.add_node("planner", planner)
    g.add_node("retrieve", retrieve)   # finds related code before the reviewers run
    g.add_node("reviewer", reviewer)
    g.add_node("synthesizer", synthesizer)

    g.add_edge(START, "guard")
    g.add_edge("guard", "planner")
    g.add_edge("planner", "retrieve")
    g.add_conditional_edges("retrieve", fan_out, ["reviewer"])  # Reviewer runs 1 to 3 times depending on the state["plan"], the "conditional edge", is used as an if/else statement.
    g.add_edge("reviewer", "synthesizer")  # waits for all reviewers to finish
    g.add_edge("synthesizer", END)

    return g.compile()