Feature: Placing and paying

  Scenario: A placed order is invoiced once the customer has their answer
    Given billing accepts invoices
    When the customer places order 9 of 80
    Then order 9 of 80 is kept unpaid
    And billing is asked to invoice order 9 for 80

  Scenario: An order without an amount is refused
    When the customer places order 9 of 0
    Then they are told the order is invalid
    And billing is asked to invoice nothing

  Scenario: A paid order has nothing left to pay
    Given order 7 of 120 is placed
    And billing lists order 7 paid
    When the shop settles its payments
    And the customer opens order 7
    Then they see 0 to pay

  Scenario: An order is cancelled by hand
    Given order 7 of 120 is placed
    When someone cancels order 7 by hand
    Then order 7 is gone
