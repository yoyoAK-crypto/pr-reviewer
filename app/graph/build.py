from langgraph.graph import StateGraph, START, END
from app.graph.state import ReviewState
from app.graph.nodes import planner, fan_out, reviewer, synthesizer

def build_graph():
    g = StateGraph(ReviewState)

    g.add_node("planner", planner)
    g.add_node("reviewer", reviewer)
    g.add_node("synthesizer", synthesizer)

    g.add_edge(START, "planner")
    g.add_conditional_edges("planner", fan_out, ["reviewer"])  # Reviewer runs 1 to 3 times depending on the state["plan"], the "conditional edge", is used as an if/else statement.
    g.add_edge("reviewer", "synthesizer")  # waits for all reviewers to finish
    g.add_edge("synthesizer", END)

    return g.compile()