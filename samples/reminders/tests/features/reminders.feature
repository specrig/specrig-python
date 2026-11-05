Feature: Reminders

  Scenario: A customer whose invoice is overdue is reminded
    Given billing holds an unpaid invoice of 120 for order 7, due yesterday
    And billing accepts reminders
    When the reminders run
    Then billing is asked to remind the customer of 120 for order 7
    And order 7 is noted as reminded of 120

  Scenario: An invoice not yet due is not reminded of
    Given billing holds an unpaid invoice of 120 for order 7, due tomorrow
    When the reminders run
    Then billing is asked to remind nobody

  # A customer is reminded at most once a day, however often the reminders run.
  Scenario: A customer reminded today is not reminded again
    Given billing holds an unpaid invoice of 120 for order 7, due yesterday
    And billing accepts reminders
    And the reminders have run
    When the reminders run
    Then billing is asked to remind the customer of 120 for order 7 once

  Scenario: The reminders are run by hand
    Given billing holds an unpaid invoice of 80 for order 9, due yesterday
    And billing accepts reminders
    When someone runs the reminders by hand
    Then billing is asked to remind the customer of 80 for order 9
