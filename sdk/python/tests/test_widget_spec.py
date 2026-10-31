from nodvard_sdk import ListItem, ListView, WidgetAction, WidgetSpec


def test_a_button_may_need_more_than_its_widget():
    action = WidgetAction(id="manage", label="Verwalten", endpoint="x", permissions=["soc.manage"])
    spec = WidgetSpec(
        id="w", title="w", data_endpoint="x", permissions=["soc.read"],
        view=ListView(item=ListItem(title="t", actions=[action])),
    )
    assert spec.view.item.actions[0].permissions == ["soc.manage"]
