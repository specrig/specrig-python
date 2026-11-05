Feature: Orders

  Scenario: The customer sees what is left to pay
    Given order 7 of 120 is placed
    And billing holds an invoice of 120 for order 7
    When the customer opens order 7
    Then they see 120 to pay

  Scenario: An order the shop does not have is refused
    When the customer opens order 8
    Then they are told there is no such order

  Scenario: The customer is told to come back while the shop's database is down
    Given order 7 of 120 is placed
    And the shop's database is down
    When the customer opens order 7
    Then they are told to come back later

  Scenario: The customer is told to come back while billing does not answer
    Given order 7 of 120 is placed
    And billing does not answer in time
    When the customer opens order 7
    Then they are told to come back later
