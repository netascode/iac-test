*** Settings ***
Documentation   Test1

*** Test Cases ***
{% for child in root.children | default([]) %}

Test {{ child.name }}
    Should Be Equal   {{ child.param is test1 child.param }}   True
{% endfor %}
