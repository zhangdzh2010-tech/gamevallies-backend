// PlayForge MongoDB Initialization Script

db = db.getSiblingDB('gamevallies');

// Create game_bundles collection with schema validation
db.createCollection('game_bundles', {
  validator: {
    $jsonSchema: {
      bsonType: 'object',
      required: ['game_id', 'version', 'html_code', 'created_at'],
      properties: {
        game_id: { bsonType: 'string', description: 'UUID reference to PostgreSQL games table' },
        version: { bsonType: 'int', minimum: 1 },
        html_code: { bsonType: 'string', description: 'Full HTML5 game code' },
        spec: {
          bsonType: 'object',
          properties: {
            game_type: { bsonType: 'string' },
            mechanics: { bsonType: 'array' },
            visual_style: { bsonType: 'object' },
            entities: { bsonType: 'array' },
            rules: { bsonType: 'object' },
            difficulty_curve: { bsonType: 'string' },
            audio_style: { bsonType: 'string' }
          }
        },
        ai_conversation: {
          bsonType: 'array',
          items: {
            bsonType: 'object',
            properties: {
              role: { enum: ['user', 'assistant', 'system'] },
              content: { bsonType: 'string' },
              ts: { bsonType: 'date' }
            }
          }
        },
        generation_meta: {
          bsonType: 'object',
          properties: {
            strategy: { enum: ['template', 'full_generation', 'hybrid'] },
            template_id: { bsonType: 'string' },
            model: { bsonType: 'string' },
            tokens_used: { bsonType: 'int' },
            generation_time_ms: { bsonType: 'int' },
            qa_passed: { bsonType: 'bool' },
            qa_retries: { bsonType: 'int' }
          }
        },
        code_size_bytes: { bsonType: 'int' },
        created_at: { bsonType: 'date' }
      }
    }
  }
});

// Create indexes
db.game_bundles.createIndex({ game_id: 1, version: -1 }, { unique: true });
db.game_bundles.createIndex({ game_id: 1 });
db.game_bundles.createIndex({ created_at: -1 });

// Create game_templates collection
db.createCollection('game_templates');
db.game_templates.createIndex({ template_id: 1 }, { unique: true });
db.game_templates.createIndex({ game_type: 1 });

print('PlayForge MongoDB initialization complete.');
