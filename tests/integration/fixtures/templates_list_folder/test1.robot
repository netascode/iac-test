{# iterate_list_folder root.children name child_name #}
*** Settings ***
Documentation   Test1

*** Test Cases ***
{% for child in root.children | default([]) %}
{% if child.name == child_name %}

Test {{ child.name }}
    Should Be Equal   {{ child.param }}   value
{% endif %}
{% endfor %}
