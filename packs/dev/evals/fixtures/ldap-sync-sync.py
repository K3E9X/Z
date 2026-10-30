"""Map directory entries of the NOC on-call groups to Mattermost team members."""


def members_to_add(directory: set[str], team: set[str]) -> list[str]:
    return sorted(directory - team)


def members_to_remove(directory: set[str], team: set[str]) -> list[str]:
    return sorted(team - directory)
