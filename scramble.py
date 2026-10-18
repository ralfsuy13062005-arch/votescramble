"""
Scramble & Redistribution Algorithm

Rules:
1. No participant receives their own vote (author_id != recipient_id), unless:
   - There is only 1 participant in the room.
2. If there are more votes than active participants (|V| > |P|), surplus votes are distributed
   fairly among active participants, while still never giving anyone their own vote.
3. If there are fewer votes than active participants, as many participants as possible get 1 vote.
4. Randomized backtracking guarantees unpredictable results and optimal valid assignments.
"""

import random
from typing import Dict, List, Any


def scramble_votes(participants: List[Dict[str, Any]], votes: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Distributes votes among active participants.
    
    Args:
        participants: List of active participant dicts with at least 'id' and 'name'.
        votes: List of vote dicts with at least 'id', 'author_id', and 'text'.
        
    Returns:
        Dict mapping participant 'id' -> list of assigned vote dicts.
    """
    if not participants:
        return {}

    # Initialize empty assignment list for every active participant
    assignments: Dict[str, List[Dict[str, Any]]] = {p["id"]: [] for p in participants}

    if not votes:
        return assignments

    # Edge case: Exactly 1 participant
    if len(participants) == 1:
        assignments[participants[0]["id"]] = list(votes)
        return assignments

    # Target counts per participant
    num_p = len(participants)
    num_v = len(votes)
    
    base_quota = num_v // num_p
    remainder = num_v % num_p
    
    # Randomly select which participants get remainder extra votes
    participant_ids = [p["id"] for p in participants]
    random.shuffle(participant_ids)
    
    capacities: Dict[str, int] = {}
    for i, pid in enumerate(participant_ids):
        capacities[pid] = base_quota + (1 if i < remainder else 0)

    # Shuffled votes for randomness
    shuffled_votes = list(votes)
    random.shuffle(shuffled_votes)

    # Sort votes: prioritize votes authored by active participants with strict constraints
    # (Votes authored by participants who left are easier to place since anyone can take them)
    active_pids = set(participant_ids)
    shuffled_votes.sort(key=lambda v: 0 if v.get("author_id") in active_pids else 1)

    result_assignment: Dict[str, List[Dict[str, Any]]] = {pid: [] for pid in participant_ids}

    def backtrack(vote_idx: int) -> bool:
        if vote_idx == len(shuffled_votes):
            return True

        current_vote = shuffled_votes[vote_idx]
        author_id = current_vote.get("author_id")

        # Find eligible participants who haven't reached capacity and aren't the author
        candidate_pids = [
            pid for pid in participant_ids
            if pid != author_id and len(result_assignment[pid]) < capacities[pid]
        ]
        
        # Shuffle candidates to randomize choices
        random.shuffle(candidate_pids)
        # Prioritize candidates who currently have the fewest assigned votes
        candidate_pids.sort(key=lambda pid: len(result_assignment[pid]))

        for pid in candidate_pids:
            result_assignment[pid].append(current_vote)
            if backtrack(vote_idx + 1):
                return True
            result_assignment[pid].pop()

        return False

    # Attempt to solve with strict no-self-vote constraint
    solved = backtrack(0)

    if not solved:
        # Fallback in extremely constrained situations (e.g. all votes authored by the same person):
        # Relax capacity and place remaining votes where author_id != pid, or anywhere if impossible
        result_assignment = {pid: [] for pid in participant_ids}
        remaining_votes = list(shuffled_votes)
        
        for vote in remaining_votes:
            author_id = vote.get("author_id")
            # Candidates who are not the author
            valid_pids = [pid for pid in participant_ids if pid != author_id]
            if not valid_pids:
                valid_pids = participant_ids  # absolute last resort
            
            # Choose the valid candidate with fewest votes
            chosen_pid = min(valid_pids, key=lambda pid: len(result_assignment[pid]))
            result_assignment[chosen_pid].append(vote)

    return result_assignment
