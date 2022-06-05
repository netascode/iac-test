class Filter:
    name = "filter1"

    @classmethod
    def filter(cls, data):
        return str(data) + "_filtered"
