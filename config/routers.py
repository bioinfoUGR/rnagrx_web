class MisraDbRouter:
    """Send the unmanaged models of the `misradb` app to the read-only misradb2 database.

    The database user only has SELECT on misradb2, so writes would fail on the server anyway;
    routing them there (instead of silently into the default DB) keeps that failure visible.
    """

    app_label = "misradb"
    alias = "misra"

    def db_for_read(self, model, **hints):
        if model._meta.app_label == self.app_label:
            return self.alias
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label == self.app_label:
            return self.alias
        return None

    def allow_relation(self, obj1, obj2, **hints):
        if self.app_label in (obj1._meta.app_label, obj2._meta.app_label):
            return obj1._meta.app_label == obj2._meta.app_label
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == self.app_label:
            return False
        return db == "default"
