"""Server-rendered pages. Each module owns one area of the site."""

from . import (pages_daily, pages_insights, pages_interview, pages_learn, pages_practice, pages_projects, pages_revision,
               pages_skills, pages_targets, pages_tracker)

PAGE_ROUTERS = [pages_daily.router, pages_learn.router, pages_revision.router, pages_practice.router,
                pages_interview.router, pages_skills.router, pages_projects.router, pages_targets.router,
                pages_tracker.router, pages_insights.router]
