from receptionist.reminders.service import _email_to_display_name


def test_email_to_display_name_splits_dotted_local_part():
    assert _email_to_display_name("john.doe@example.com") == "John Doe"


def test_email_to_display_name_splits_camel_case_local_part():
    assert _email_to_display_name("johnDoe@example.com") == "John Doe"


def test_email_to_display_name_splits_plain_firstlast_local_part():
    assert _email_to_display_name("johndoe@example.com") == "John Doe"


def test_email_to_display_name_ignores_plus_tag():
    assert _email_to_display_name("jane.doe+new@example.com") == "Jane Doe"
