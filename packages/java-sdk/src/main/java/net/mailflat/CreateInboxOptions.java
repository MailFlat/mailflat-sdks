// CreateInboxOptions — fluent builder for MailFlat.create(...).
//
// Connected to:
//   - used by:    MailFlat.create(CreateInboxOptions), user code
//   - depends on: yok
//
// Key export: CreateInboxOptions.builder()...build()
package net.mailflat;

/** Optional parameters for opening an inbox. All fields are optional. */
public final class CreateInboxOptions {
    final String prefix;
    final String label;
    final String subdomain;     // mailflat.net subdomain (random if null)
    final String domain;        // verified BYOD custom domain
    final Integer retentionHours;

    private CreateInboxOptions(Builder b) {
        this.prefix = b.prefix;
        this.label = b.label;
        this.subdomain = b.subdomain;
        this.domain = b.domain;
        this.retentionHours = b.retentionHours;
    }

    public static Builder builder() {
        return new Builder();
    }

    public static final class Builder {
        private String prefix;
        private String label;
        private String subdomain;
        private String domain;
        private Integer retentionHours;

        public Builder prefix(String v)         { this.prefix = v; return this; }
        /** Display name. Paid plans only: on free the server reports it in {@code ignored_fields}. */
        public Builder label(String v)          { this.label = v; return this; }
        public Builder subdomain(String v)      { this.subdomain = v; return this; }
        public Builder domain(String v)         { this.domain = v; return this; }
        /** Retention in hours; capped to your plan's maximum if higher. */
        public Builder retentionHours(int v)    { this.retentionHours = v; return this; }

        public CreateInboxOptions build() {
            return new CreateInboxOptions(this);
        }
    }
}
