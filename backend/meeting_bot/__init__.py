from meeting_bot.daily_bot import InterviewBot

# Dictionary to hold metadata for the interview bot
META_DATA = {
  "greeting": "Hello! Welcome to today's interview. We'll be discussing your skills and experiences, and I'll guide you through a flow that's designed to be conversational and informative.",
  "question": {
    "type": "new",
    "question": "Let's say you're working on a project and you realize that one of the team members is working on a task that you had assigned to someone else. How would you handle this situation and ensure that the work gets done efficiently?"
  },
  "other_key": "other value"
}

__all__ = ["InterviewBot", "META_DATA"]

